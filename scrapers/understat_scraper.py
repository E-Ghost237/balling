"""
Understat scraper — pulls league/team/match/shot-level xG data from
understat.com and loads it into the football.db SQLite database.

Understat has no official API. Data is embedded as JSON inside <script>
tags on each page (e.g. `var teamsData = JSON.parse('...')`). This module
extracts and decodes those blocks.

NOTE: this script makes live HTTP requests to understat.com. It cannot be
test-run inside this sandbox (network egress here is restricted to package
registries like PyPI/npm/GitHub, not general websites) — run it on your own
machine. Install deps first:

    pip install requests beautifulsoup4 --break-system-packages

Usage:
    python understat_scraper.py --league EPL --season 2025 --db football.db
"""

import argparse
import json
import re
import sqlite3
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

import requests

BASE_URL = "https://understat.com"

# Understat's internal league codes
LEAGUE_SLUGS = {
    "EPL": ("Premier League", "England"),
    "La_liga": ("La Liga", "Spain"),
    "Bundesliga": ("Bundesliga", "Germany"),
    "Serie_A": ("Serie A", "Italy"),
    "Ligue_1": ("Ligue 1", "France"),
    "RFPL": ("Russian Premier League", "Russia"),
}

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    )
}

# Headers for the newer JSON endpoint (as of the site restructure around
# Dec 2025 — see get_league_season_data() docstring below for details).
API_HEADERS = {
    **HEADERS,
    "Accept": "application/json, text/javascript, */*",
    "X-Requested-With": "XMLHttpRequest",
}

# Be polite — Understat has no published rate limit but scraping fast is
# how sites start blocking you. ~1 request every 1.5s per worker is a safe
# default; MAX_SHOT_WORKERS below fetches shots on a small thread pool so
# the effective rate is ~MAX_SHOT_WORKERS x this, not fully serial.
REQUEST_DELAY_SECONDS = 1.5

# Concurrency for the --with-shots fetch only (one request per finished
# match — the slow part of a full backfill). Kept modest: high enough to
# meaningfully cut backfill time, low enough to stay well short of hammering
# the site. Match-list/team fetches stay fully serial.
MAX_SHOT_WORKERS = 4

# Reused across requests (connection pooling/keep-alive) instead of opening
# a fresh connection per call — real speedup, no change in request pattern.
SESSION = requests.Session()
SESSION.mount("https://", requests.adapters.HTTPAdapter(pool_maxsize=MAX_SHOT_WORKERS + 2))


def fetch_page(url: str, headers: dict = None) -> str:
    resp = SESSION.get(url, headers=headers or HEADERS, timeout=20)
    resp.raise_for_status()
    time.sleep(REQUEST_DELAY_SECONDS)
    return resp.text


def extract_json_var(html: str, var_name: str):
    """
    LEGACY (pre ~Dec 2025) format. Understat used to embed data like:
        var teamsData = JSON.parse('\\x7b...\\x7d');
    directly in the page's <script> tags. This pulls it out and decodes it.
    Kept as a fallback in case Understat reverts or serves this to some
    requests (e.g. non-JS clients).
    """
    pattern = rf"var\s+{var_name}\s*=\s*JSON\.parse\('(.+?)'\)"
    match = re.search(pattern, html)
    if not match:
        return None
    raw = match.group(1)
    decoded = raw.encode("utf-8").decode("unicode_escape").encode("latin1").decode("utf-8")
    return json.loads(decoded)


def get_league_season_data(league_slug: str, season: int):
    """
    Fetch team-aggregated data and the match list for a given league/season.

    Understat restructured their site around December 2025, which broke every
    scraper relying on the old `teamsData` / `datesData` script-tag embedding
    (confirmed across multiple independent scraper projects, not just ours).

    This function first tries what looks like their current JSON endpoint:
        GET https://understat.com/getLeagueData/{league_slug}/{season}
    which appears to return `{"dates": [...], "teams": {...}, "players": [...]}`
    directly as JSON. This is based on a community-reported fix, NOT official
    documentation — Understat doesn't publish an API. Verify the actual
    response shape once you can run this against the live site; the field
    names below may need adjusting.

    Falls back to the legacy script-tag scraping if the new endpoint fails,
    in case the old format is still served in some cases.
    """
    api_url = f"{BASE_URL}/getLeagueData/{league_slug}/{season}"
    try:
        resp = SESSION.get(api_url, headers=API_HEADERS, timeout=20)
        time.sleep(REQUEST_DELAY_SECONDS)
        if resp.ok:
            data = resp.json()
            dates = data.get("dates", [])
            teams = data.get("teams", {})
            if isinstance(dates, dict):
                dates = list(dates.values())
            if dates and teams:
                return teams, dates
            print("  New API endpoint returned an unexpected/empty shape — falling back to legacy scraping.")
    except (requests.RequestException, ValueError) as exc:
        print(f"  New API endpoint failed ({exc}) — falling back to legacy scraping.")

    # --- Legacy fallback: scrape embedded script tags on the league page ---
    url = f"{BASE_URL}/league/{league_slug}/{season}"
    html = fetch_page(url)
    teams_data = extract_json_var(html, "teamsData")
    dates_data = extract_json_var(html, "datesData")
    return teams_data, dates_data


def get_match_shots(match_id: str):
    """Fetch shot-level xG data for a single match."""
    url = f"{BASE_URL}/match/{match_id}"
    html = fetch_page(url)
    shots_data = extract_json_var(html, "shotsData")
    return shots_data  # dict with keys "h" and "a" (home/away shot lists)


# ---------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------

def get_or_create_league(conn, name, country, understat_slug):
    cur = conn.execute(
        "SELECT id FROM leagues WHERE name = ? AND country = ?", (name, country)
    )
    row = cur.fetchone()
    if row:
        return row[0]
    cur = conn.execute(
        "INSERT INTO leagues (name, country, kind, understat_slug) VALUES (?, ?, 'club', ?)",
        (name, country, understat_slug),
    )
    return cur.lastrowid


def get_or_create_season(conn, league_id, year_start):
    cur = conn.execute(
        "SELECT id FROM seasons WHERE league_id = ? AND year_start = ?",
        (league_id, year_start),
    )
    row = cur.fetchone()
    if row:
        return row[0]
    cur = conn.execute(
        "INSERT INTO seasons (league_id, year_start) VALUES (?, ?)",
        (league_id, year_start),
    )
    return cur.lastrowid


def get_or_create_team(conn, name, understat_id):
    # First check by understat alias
    cur = conn.execute(
        "SELECT team_id FROM team_aliases WHERE source = 'understat' AND alias = ?",
        (understat_id,),
    )
    row = cur.fetchone()
    if row:
        return row[0]

    cur = conn.execute("SELECT id FROM teams WHERE name = ?", (name,))
    row = cur.fetchone()
    if row:
        team_id = row[0]
    else:
        cur = conn.execute(
            "INSERT INTO teams (name, understat_id) VALUES (?, ?)", (name, understat_id)
        )
        team_id = cur.lastrowid

    conn.execute(
        "INSERT OR IGNORE INTO team_aliases (team_id, source, alias) VALUES (?, 'understat', ?)",
        (team_id, understat_id),
    )
    return team_id


def insert_match(conn, league_id, season_id, match, home_team_id, away_team_id):
    """
    `match` is one entry from datesData — contains id, datetime, goals (dict),
    xG (dict), isResult, etc.
    """
    is_result = match.get("isResult", False)
    status = "finished" if is_result else "scheduled"

    home_goals = int(match["goals"]["h"]) if is_result and match["goals"]["h"] is not None else None
    away_goals = int(match["goals"]["a"]) if is_result and match["goals"]["a"] is not None else None
    home_xg = float(match["xG"]["h"]) if is_result and match["xG"]["h"] is not None else None
    away_xg = float(match["xG"]["a"]) if is_result and match["xG"]["a"] is not None else None

    match_date = match.get("datetime", "")

    conn.execute(
        """
        INSERT INTO matches (
            league_id, season_id, match_date, home_team_id, away_team_id,
            home_goals, away_goals, home_xg, away_xg, status,
            is_neutral_venue, source, source_match_id
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, 'understat', ?)
        ON CONFLICT(source, source_match_id) DO UPDATE SET
            home_goals=excluded.home_goals,
            away_goals=excluded.away_goals,
            home_xg=excluded.home_xg,
            away_xg=excluded.away_xg,
            status=excluded.status
        """,
        (
            league_id, season_id, match_date, home_team_id, away_team_id,
            home_goals, away_goals, home_xg, away_xg, status, str(match["id"]),
        ),
    )


def insert_shots(conn, match_db_id, shots_data, home_team_id, away_team_id):
    if not shots_data:
        return
    for side, team_id in (("h", home_team_id), ("a", away_team_id)):
        for shot in shots_data.get(side, []):
            conn.execute(
                """
                INSERT INTO shots (match_id, team_id, player_name, minute, x, y, xg, result, situation, body_part)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    match_db_id,
                    team_id,
                    shot.get("player"),
                    int(shot.get("minute", 0)),
                    float(shot.get("X", 0)),
                    float(shot.get("Y", 0)),
                    float(shot.get("xG", 0)),
                    shot.get("result"),
                    shot.get("situation"),
                    shot.get("shotType"),
                ),
            )


# ---------------------------------------------------------------------
# Main scrape routine
# ---------------------------------------------------------------------

def scrape_league_season(conn, league_slug: str, season: int, fetch_shots: bool = False):
    if league_slug not in LEAGUE_SLUGS:
        raise ValueError(f"Unknown league slug '{league_slug}'. Options: {list(LEAGUE_SLUGS)}")

    league_name, country = LEAGUE_SLUGS[league_slug]
    print(f"Fetching {league_name} {season}/{season+1}...")

    teams_data, dates_data = get_league_season_data(league_slug, season)
    if teams_data is None or dates_data is None:
        print("  Could not extract data — Understat's page structure may have changed.")
        return

    league_id = get_or_create_league(conn, league_name, country, league_slug)
    season_id = get_or_create_season(conn, league_id, season)

    # teams_data is a dict keyed by understat team id -> {id, title, history: [...]}
    team_name_by_understat_id = {}
    for uid, tinfo in teams_data.items():
        team_id = get_or_create_team(conn, tinfo["title"], uid)
        team_name_by_understat_id[uid] = team_id
    conn.commit()

    print(f"  {len(team_name_by_understat_id)} teams, {len(dates_data)} matches")

    finished_matches = 0
    matches_needing_shots = []  # (understat_match_id, match_db_id, home_team_id, away_team_id)

    for match in dates_data:
        home_uid = str(match["h"]["id"])
        away_uid = str(match["a"]["id"])

        home_team_id = team_name_by_understat_id.get(home_uid) or get_or_create_team(
            conn, match["h"]["title"], home_uid
        )
        away_team_id = team_name_by_understat_id.get(away_uid) or get_or_create_team(
            conn, match["a"]["title"], away_uid
        )

        insert_match(conn, league_id, season_id, match, home_team_id, away_team_id)

        if match.get("isResult"):
            finished_matches += 1

        if fetch_shots and match.get("isResult"):
            cur = conn.execute(
                "SELECT id FROM matches WHERE source = 'understat' AND source_match_id = ?",
                (str(match["id"]),),
            )
            row = cur.fetchone()
            if row:
                match_db_id = row[0]
                cur2 = conn.execute("SELECT 1 FROM shots WHERE match_id = ? LIMIT 1", (match_db_id,))
                if not cur2.fetchone():
                    matches_needing_shots.append((str(match["id"]), match_db_id, home_team_id, away_team_id))

    conn.commit()

    if fetch_shots and matches_needing_shots:
        print(f"  Fetching shots for {len(matches_needing_shots)} matches ({MAX_SHOT_WORKERS} concurrent workers)...")
        done = 0
        with ThreadPoolExecutor(max_workers=MAX_SHOT_WORKERS) as executor:
            future_to_match = {
                executor.submit(get_match_shots, uid): (uid, match_db_id, home_team_id, away_team_id)
                for uid, match_db_id, home_team_id, away_team_id in matches_needing_shots
            }
            for future in as_completed(future_to_match):
                uid, match_db_id, home_team_id, away_team_id = future_to_match[future]
                try:
                    shots_data = future.result()
                    insert_shots(conn, match_db_id, shots_data, home_team_id, away_team_id)
                except Exception as exc:
                    print(f"    Failed to fetch shots for match {uid}: {exc}")
                done += 1
                if done % 20 == 0 or done == len(matches_needing_shots):
                    conn.commit()  # commit incrementally so Ctrl+C only loses a few in-flight matches, not the whole season
                if done % 50 == 0 or done == len(matches_needing_shots):
                    print(f"    {done}/{len(matches_needing_shots)} done")

    print(f"  Done. {finished_matches} finished matches loaded" + (" (with shots)" if fetch_shots else "."))


def main():
    parser = argparse.ArgumentParser(description="Scrape Understat data into football.db")
    parser.add_argument("--league", required=True, choices=list(LEAGUE_SLUGS), help="Understat league slug")
    parser.add_argument("--season", required=True, type=int, help="Season start year, e.g. 2025 for 2025/26")
    parser.add_argument("--db", default="data/football.db", help="Path to SQLite database")
    parser.add_argument(
        "--with-shots",
        action="store_true",
        help="Also fetch shot-level xG per match (much slower — one extra request per match)",
    )
    args = parser.parse_args()

    conn = sqlite3.connect(args.db)
    conn.execute("PRAGMA foreign_keys = ON")

    try:
        scrape_league_season(conn, args.league, args.season, fetch_shots=args.with_shots)
    finally:
        conn.close()


if __name__ == "__main__":
    main()