"""
Loads UEFA Champions League match data from football-data.org into
football.db. Requires a free API key — register at:
    https://www.football-data.org/client/register

No xG in this data — home_xg/away_xg stored as NULL, same as the
football-data.co.uk domestic leagues.

Usage:
    python footballdata_org_loader.py --db football.db --api-key YOUR_KEY --from-season 2014 --to-season 2025

    Or set the key as an environment variable to avoid typing it each time:
    export FD_ORG_API_KEY=your_key_here
    python footballdata_org_loader.py --db football.db
"""

import argparse
import os
import sqlite3
import time

import requests

from leagues_config import CONTINENTAL_COMPETITIONS

API_BASE = "https://api.football-data.org/v4"

# Free tier is 10 requests/minute — 6.5s between calls stays safely under that.
REQUEST_DELAY_SECONDS = 6.5


def get_or_create_league(conn, name, country, kind="club"):
    cur = conn.execute(
        "SELECT id FROM leagues WHERE name = ? AND (country = ? OR (country IS NULL AND ? IS NULL))",
        (name, country, country),
    )
    row = cur.fetchone()
    if row:
        return row[0]
    cur = conn.execute(
        "INSERT INTO leagues (name, country, kind, fd_org_code) VALUES (?, ?, ?, ?)",
        (name, country, kind, "CL"),
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


def get_or_create_team(conn, name, source="football-data-org"):
    """
    NOTE — known limitation: Champions League teams overlap with clubs
    already loaded from Understat (e.g. "Manchester City" vs "Manchester
    City FC"). This does exact-name matching only, so the same real-world
    club may end up as two separate team rows if the naming differs across
    sources. Fine for now (Champions League stats stay self-consistent),
    but before merging CL data into a club's overall Elo/attack-defense
    rating, you'll want a manual alias reconciliation pass — check
    team_aliases for near-duplicate team names across sources.
    """
    cur = conn.execute(
        "SELECT team_id FROM team_aliases WHERE source = ? AND alias = ?",
        (source, name),
    )
    row = cur.fetchone()
    if row:
        return row[0]

    cur = conn.execute("SELECT id FROM teams WHERE name = ?", (name,))
    row = cur.fetchone()
    if row:
        team_id = row[0]
    else:
        cur = conn.execute("INSERT INTO teams (name) VALUES (?)", (name,))
        team_id = cur.lastrowid

    conn.execute(
        "INSERT OR IGNORE INTO team_aliases (team_id, source, alias) VALUES (?, ?, ?)",
        (team_id, source, name),
    )
    return team_id


def insert_match(conn, league_id, season_id, match):
    home = match["homeTeam"]["name"]
    away = match["awayTeam"]["name"]
    status = "finished" if match["status"] == "FINISHED" else "scheduled"

    home_goals = away_goals = None
    if status == "finished":
        score = match.get("score", {}).get("fullTime", {})
        home_goals = score.get("home")
        away_goals = score.get("away")

    home_id = get_or_create_team(conn, home)
    away_id = get_or_create_team(conn, away)

    conn.execute(
        """
        INSERT INTO matches (
            league_id, season_id, match_date, home_team_id, away_team_id,
            home_goals, away_goals, home_xg, away_xg, status,
            is_neutral_venue, source, source_match_id
        ) VALUES (?, ?, ?, ?, ?, ?, ?, NULL, NULL, ?, 0, 'football-data-org', ?)
        ON CONFLICT(source, source_match_id) DO UPDATE SET
            home_goals=excluded.home_goals,
            away_goals=excluded.away_goals,
            status=excluded.status
        """,
        (
            league_id, season_id, match.get("utcDate", ""), home_id, away_id,
            home_goals, away_goals, status, str(match["id"]),
        ),
    )


def load_champions_league(conn, api_key, from_season, to_season):
    """
    IMPORTANT: football-data.org's FREE tier only allows access to the
    CURRENT season — historical seasons are gated behind a paid "historical
    package" add-on (confirmed via a live 403 response: "resource... not
    within your permissions... check your subscription"). Looping back to
    2014 will just burn through 403s.

    So this only actually attempts `to_season` (the most recent one) by
    default, and stops immediately on a 403 rather than wasting requests
    retrying years that are structurally unavailable on a free key.
    """
    cfg = CONTINENTAL_COMPETITIONS["Champions_League"]
    league_id = get_or_create_league(conn, cfg["name"], cfg["country"], cfg["kind"])
    headers = {"X-Auth-Token": api_key}

    total_loaded = 0
    for year in range(to_season, from_season - 1, -1):  # most recent first
        url = f"{API_BASE}/competitions/CL/matches"
        print(f"Fetching Champions League {year}/{year+1}...")
        try:
            resp = requests.get(url, headers=headers, params={"season": year}, timeout=20)
        except requests.RequestException as exc:
            print(f"  Request failed: {exc}")
            time.sleep(REQUEST_DELAY_SECONDS)
            continue

        time.sleep(REQUEST_DELAY_SECONDS)

        if resp.status_code == 429:
            print("  Rate limited — waiting 60s and retrying once...")
            time.sleep(60)
            resp = requests.get(url, headers=headers, params={"season": year}, timeout=20)
            time.sleep(REQUEST_DELAY_SECONDS)

        if resp.status_code == 403:
            print(f"  HTTP 403 — free tier doesn't allow season {year} (historical seasons need a paid add-on).")
            print("  Stopping here rather than burning more requests on years that will also 403.")
            break

        if not resp.ok:
            print(f"  Failed: HTTP {resp.status_code} — {resp.text[:200]}")
            continue

        data = resp.json()
        matches = data.get("matches", [])
        if not matches:
            print(f"  No matches returned for {year} — may be outside API's available range.")
            continue

        season_id = get_or_create_season(conn, league_id, year)
        for match in matches:
            insert_match(conn, league_id, season_id, match)
        conn.commit()

        finished = sum(1 for m in matches if m["status"] == "FINISHED")
        print(f"  {len(matches)} matches ({finished} finished) loaded")
        total_loaded += len(matches)

    return total_loaded


def main():
    parser = argparse.ArgumentParser(description="Load Champions League data from football-data.org")
    parser.add_argument("--db", default="data/football.db")
    parser.add_argument("--api-key", default=os.environ.get("FD_ORG_API_KEY"))
    parser.add_argument("--from-season", type=int, default=2014)
    parser.add_argument("--to-season", type=int, default=2025)
    args = parser.parse_args()

    if not args.api_key:
        print("No API key provided. Pass --api-key YOUR_KEY or set FD_ORG_API_KEY.")
        print("Register for free at: https://www.football-data.org/client/register")
        return

    conn = sqlite3.connect(args.db)
    conn.execute("PRAGMA foreign_keys = ON")

    total = load_champions_league(conn, args.api_key, args.from_season, args.to_season)
    conn.close()
    print(f"\nDone. {total} total matches loaded/updated.")


if __name__ == "__main__":
    main()
