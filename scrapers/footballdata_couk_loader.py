"""
Loads domestic league data from football-data.co.uk CSV files into
football.db. Covers the 9 leagues in leagues_config.DOMESTIC_LEAGUES.

These files have no xG — home_xg/away_xg are stored as NULL. Treat any
predictions built on these leagues as 'goals_only_fallback' confidence.

Usage:
    python footballdata_couk_loader.py --db football.db --from-season 2014 --to-season 2025
    python footballdata_couk_loader.py --db football.db --leagues Championship Eredivisie
"""

import argparse
import csv
import io
import sqlite3
import time
from datetime import datetime

import requests

from leagues_config import DOMESTIC_LEAGUES


def current_season_start_year() -> int:
    """Same convention used below for parsing the 'Season' column and in
    batch_scrape.py: European seasons run August-May, so treat July onward
    as already being in the new season year."""
    now = datetime.now()
    return now.year if now.month >= 7 else now.year - 1

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    )
}
REQUEST_DELAY_SECONDS = 1.0

# Column name candidates per logical field — main-league and extra-league
# files use different headers (confirmed by diagnostic: main uses
# HomeTeam/AwayTeam/FTHG/FTAG, extra uses Home/Away/HG/AG).
COLUMN_CANDIDATES = {
    "home_team": ["HomeTeam", "Home"],
    "away_team": ["AwayTeam", "Away"],
    "home_goals": ["FTHG", "HG"],
    "away_goals": ["FTAG", "AG"],
    "date": ["Date"],
}


def season_code(year_start: int) -> str:
    """2025 -> '2526' (for the 2025/26 season)."""
    y1 = year_start % 100
    y2 = (year_start + 1) % 100
    return f"{y1:02d}{y2:02d}"


def fetch_csv_rows(url: str, max_retries: int = 3):
    for attempt in range(1, max_retries + 1):
        try:
            resp = requests.get(url, headers=HEADERS, timeout=20)
            time.sleep(REQUEST_DELAY_SECONDS)
            if not resp.ok:
                return None
            # utf-8-sig strips the BOM ("ï»¿") seen on the first column in diagnostics
            text = resp.content.decode("utf-8-sig", errors="replace")
            reader = csv.DictReader(io.StringIO(text))
            return list(reader)
        except requests.exceptions.RequestException as exc:
            if attempt == max_retries:
                print(f"    Failed after {max_retries} attempts ({exc}) — skipping this file.")
                return None
            wait = 3 * attempt
            print(f"    Connection error ({exc}) — retrying in {wait}s (attempt {attempt}/{max_retries})...")
            time.sleep(wait)
    return None


def resolve_column(row: dict, field: str):
    for candidate in COLUMN_CANDIDATES[field]:
        if candidate in row and row[candidate] not in (None, ""):
            return row[candidate]
    return None


def parse_date(raw: str):
    """Handles both DD/MM/YYYY and DD/MM/YY, seen across different years' files."""
    for fmt in ("%d/%m/%Y", "%d/%m/%y"):
        try:
            return datetime.strptime(raw.strip(), fmt)
        except (ValueError, AttributeError):
            continue
    return None


# ---------------------------------------------------------------------
# DB helpers (mirrors understat_scraper.py's approach)
# ---------------------------------------------------------------------

def get_or_create_league(conn, name, country, kind="club"):
    cur = conn.execute(
        "SELECT id FROM leagues WHERE name = ? AND (country = ? OR (country IS NULL AND ? IS NULL))",
        (name, country, country),
    )
    row = cur.fetchone()
    if row:
        return row[0]
    cur = conn.execute(
        "INSERT INTO leagues (name, country, kind) VALUES (?, ?, ?)",
        (name, country, kind),
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


def get_or_create_team(conn, name, source):
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


def insert_match(conn, league_id, season_id, home_team_id, away_team_id,
                  match_date, home_goals, away_goals, source, source_match_id):
    conn.execute(
        """
        INSERT INTO matches (
            league_id, season_id, match_date, home_team_id, away_team_id,
            home_goals, away_goals, home_xg, away_xg, status,
            is_neutral_venue, source, source_match_id
        ) VALUES (?, ?, ?, ?, ?, ?, ?, NULL, NULL, 'finished', 0, ?, ?)
        ON CONFLICT(source, source_match_id) DO UPDATE SET
            home_goals=excluded.home_goals,
            away_goals=excluded.away_goals
        """,
        (league_id, season_id, match_date, home_team_id, away_team_id,
         home_goals, away_goals, source, source_match_id),
    )


# ---------------------------------------------------------------------
# Loaders
# ---------------------------------------------------------------------

def load_main_league(conn, league_key, cfg, from_season, to_season):
    league_id = get_or_create_league(conn, cfg["name"], cfg["country"], cfg["kind"])
    loaded_total = 0

    for year in range(from_season, to_season + 1):
        url = f"https://www.football-data.co.uk/mmz4281/{season_code(year)}/{cfg['code']}.csv"
        rows = fetch_csv_rows(url)
        if not rows:
            print(f"    {cfg['name']} {year}/{year+1}: no data (404 or empty) — skipping")
            continue

        season_id = get_or_create_season(conn, league_id, year)
        count = 0
        for i, row in enumerate(rows):
            home_name = resolve_column(row, "home_team")
            away_name = resolve_column(row, "away_team")
            home_goals_raw = resolve_column(row, "home_goals")
            away_goals_raw = resolve_column(row, "away_goals")
            date_raw = resolve_column(row, "date")
            if not all([home_name, away_name, home_goals_raw, away_goals_raw, date_raw]):
                continue
            parsed_date = parse_date(date_raw)
            if parsed_date is None:
                continue

            home_id = get_or_create_team(conn, home_name, "football-data-couk")
            away_id = get_or_create_team(conn, away_name, "football-data-couk")
            source_match_id = f"{cfg['code']}_{year}_{i}"

            insert_match(
                conn, league_id, season_id, home_id, away_id,
                parsed_date.strftime("%Y-%m-%d"),
                int(home_goals_raw), int(away_goals_raw),
                "football-data-couk", source_match_id,
            )
            count += 1

        conn.commit()
        loaded_total += count
        print(f"    {cfg['name']} {year}/{year+1}: {count} matches loaded")

    return loaded_total


def load_extra_league(conn, league_key, cfg):
    league_id = get_or_create_league(conn, cfg["name"], cfg["country"], cfg["kind"])
    url = f"https://www.football-data.co.uk/new/{cfg['code']}.csv"
    rows = fetch_csv_rows(url)
    if not rows:
        print(f"    {cfg['name']}: no data (404 or empty)")
        return 0

    count_by_season = {}
    for i, row in enumerate(rows):
        home_name = resolve_column(row, "home_team")
        away_name = resolve_column(row, "away_team")
        home_goals_raw = resolve_column(row, "home_goals")
        away_goals_raw = resolve_column(row, "away_goals")
        date_raw = resolve_column(row, "date")
        if not all([home_name, away_name, home_goals_raw, away_goals_raw, date_raw]):
            continue
        parsed_date = parse_date(date_raw)
        if parsed_date is None:
            continue

        # "Season" column looks like "2012/2013" — use the start year
        season_raw = row.get("Season", "")
        try:
            year_start = int(season_raw.split("/")[0])
        except (ValueError, IndexError):
            year_start = parsed_date.year if parsed_date.month >= 7 else parsed_date.year - 1

        season_id = get_or_create_season(conn, league_id, year_start)
        home_id = get_or_create_team(conn, home_name, "football-data-couk")
        away_id = get_or_create_team(conn, away_name, "football-data-couk")
        source_match_id = f"{cfg['code']}_{i}"

        insert_match(
            conn, league_id, season_id, home_id, away_id,
            parsed_date.strftime("%Y-%m-%d"),
            int(home_goals_raw), int(away_goals_raw),
            "football-data-couk", source_match_id,
        )
        count_by_season[year_start] = count_by_season.get(year_start, 0) + 1

    conn.commit()
    total = sum(count_by_season.values())
    print(f"    {cfg['name']}: {total} matches loaded across {len(count_by_season)} seasons")
    return total


def main():
    parser = argparse.ArgumentParser(description="Load football-data.co.uk domestic leagues into football.db")
    parser.add_argument("--db", default="data/football.db")
    parser.add_argument("--from-season", type=int, default=2014)
    parser.add_argument("--to-season", type=int, default=current_season_start_year())
    parser.add_argument(
        "--leagues",
        nargs="+",
        default=list(DOMESTIC_LEAGUES.keys()),
        help=f"Subset of leagues to load. Options: {list(DOMESTIC_LEAGUES.keys())}",
    )
    args = parser.parse_args()

    conn = sqlite3.connect(args.db)
    conn.execute("PRAGMA foreign_keys = ON")

    grand_total = 0
    failures = []
    for league_key in args.leagues:
        if league_key not in DOMESTIC_LEAGUES:
            print(f"Unknown league '{league_key}', skipping")
            continue
        cfg = DOMESTIC_LEAGUES[league_key]
        print(f"\n=== {cfg['name']} ({cfg['country']}) ===")
        try:
            if cfg["source"] == "couk_main":
                grand_total += load_main_league(conn, league_key, cfg, args.from_season, args.to_season)
            elif cfg["source"] == "couk_extra":
                grand_total += load_extra_league(conn, league_key, cfg)
        except Exception as exc:
            print(f"  FAILED: {exc}")
            failures.append((league_key, str(exc)))

    conn.close()
    print(f"\nDone. {grand_total} total matches loaded/updated.")
    if failures:
        print(f"\n{len(failures)} league(s) failed entirely:")
        for league_key, err in failures:
            print(f"  {league_key}: {err}")
        print("Re-run with --leagues <failed_league> to retry just those.")


if __name__ == "__main__":
    main()