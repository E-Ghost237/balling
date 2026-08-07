"""
Loads national-team match data from the martj42/international_results
dataset (free, actively-updated, no API key) — covers the competitions in
leagues_config.INTERNATIONAL_COMPETITIONS: World Cup / Euro / AFCON /
Copa América / Gold Cup / AFC Asian Cup, each split into the tournament
proper and its qualifiers.

Source: https://raw.githubusercontent.com/martj42/international_results/master/results.csv
~49k matches, 1872-present, including already-scheduled future fixtures
(scores 'NA' -> stored as status='scheduled').

No xG is available here — home_xg/away_xg stay NULL, same as the couk
domestic leagues (goals_only_fallback).

Usage:
    python international_results_loader.py --db data/football.db
    python international_results_loader.py --db data/football.db --competitions AFCON Copa_America
"""

import argparse
import csv
import io
import sqlite3

import requests

from leagues_config import INTERNATIONAL_COMPETITIONS

CSV_URL = "https://raw.githubusercontent.com/martj42/international_results/master/results.csv"
SOURCE = "international_results"


def fetch_csv_rows(url: str):
    resp = requests.get(url, timeout=30)
    resp.raise_for_status()
    reader = csv.DictReader(io.StringIO(resp.text))
    return list(reader)


def build_tournament_lookup(competition_keys):
    """Maps each CSV `tournament` string to the competition key that covers it."""
    lookup = {}
    for key in competition_keys:
        cfg = INTERNATIONAL_COMPETITIONS[key]
        for value in cfg["tournament_values"]:
            lookup[value] = key
    return lookup


def get_or_create_league(conn, name, kind="international"):
    cur = conn.execute("SELECT id FROM leagues WHERE name = ? AND country IS NULL", (name,))
    row = cur.fetchone()
    if row:
        return row[0]
    cur = conn.execute(
        "INSERT INTO leagues (name, country, kind) VALUES (?, NULL, ?)", (name, kind)
    )
    return cur.lastrowid


def get_or_create_season(conn, league_id, year_start):
    cur = conn.execute(
        "SELECT id FROM seasons WHERE league_id = ? AND year_start = ?", (league_id, year_start)
    )
    row = cur.fetchone()
    if row:
        return row[0]
    cur = conn.execute(
        "INSERT INTO seasons (league_id, year_start) VALUES (?, ?)", (league_id, year_start)
    )
    return cur.lastrowid


def get_or_create_team(conn, name):
    """National teams are keyed by name with country set to the same value
    (e.g. team 'France' / country 'France') — distinct from any club team,
    which is always keyed by its actual club country."""
    cur = conn.execute(
        "SELECT team_id FROM team_aliases WHERE source = ? AND alias = ?", (SOURCE, name)
    )
    row = cur.fetchone()
    if row:
        return row[0]

    cur = conn.execute("SELECT id FROM teams WHERE name = ? AND country = ?", (name, name))
    row = cur.fetchone()
    if row:
        team_id = row[0]
    else:
        cur = conn.execute("INSERT INTO teams (name, country) VALUES (?, ?)", (name, name))
        team_id = cur.lastrowid

    conn.execute(
        "INSERT OR IGNORE INTO team_aliases (team_id, source, alias) VALUES (?, ?, ?)",
        (team_id, SOURCE, name),
    )
    return team_id


def insert_match(conn, league_id, season_id, home_id, away_id, row, source_match_id):
    match_date = row["date"]
    is_finished = row["home_score"] not in (None, "", "NA") and row["away_score"] not in (None, "", "NA")
    status = "finished" if is_finished else "scheduled"
    home_goals = int(row["home_score"]) if is_finished else None
    away_goals = int(row["away_score"]) if is_finished else None
    is_neutral = 1 if row.get("neutral", "").strip().upper() == "TRUE" else 0

    conn.execute(
        """
        INSERT INTO matches (
            league_id, season_id, match_date, home_team_id, away_team_id,
            home_goals, away_goals, home_xg, away_xg, status,
            is_neutral_venue, source, source_match_id
        ) VALUES (?, ?, ?, ?, ?, ?, ?, NULL, NULL, ?, ?, ?, ?)
        ON CONFLICT(source, source_match_id) DO UPDATE SET
            home_goals=excluded.home_goals,
            away_goals=excluded.away_goals,
            status=excluded.status
        """,
        (league_id, season_id, match_date, home_id, away_id,
         home_goals, away_goals, status, is_neutral, SOURCE, source_match_id),
    )


def load(conn, rows, competition_keys):
    tournament_lookup = build_tournament_lookup(competition_keys)
    league_ids = {key: get_or_create_league(conn, INTERNATIONAL_COMPETITIONS[key]["name"])
                  for key in competition_keys}

    counts = {key: 0 for key in competition_keys}
    for i, row in enumerate(rows):
        key = tournament_lookup.get(row.get("tournament", ""))
        if key is None:
            continue

        year_start = int(row["date"][:4])
        league_id = league_ids[key]
        season_id = get_or_create_season(conn, league_id, year_start)

        home_id = get_or_create_team(conn, row["home_team"])
        away_id = get_or_create_team(conn, row["away_team"])
        source_match_id = f"{row['date']}|{row['home_team']}|{row['away_team']}|{row['tournament']}"

        insert_match(conn, league_id, season_id, home_id, away_id, row, source_match_id)
        counts[key] += 1

    conn.commit()
    return counts


def main():
    parser = argparse.ArgumentParser(description="Load international_results.csv into football.db")
    parser.add_argument("--db", default="data/football.db")
    parser.add_argument(
        "--competitions",
        nargs="+",
        default=list(INTERNATIONAL_COMPETITIONS.keys()),
        help=f"Subset of competitions to load. Options: {list(INTERNATIONAL_COMPETITIONS.keys())}",
    )
    args = parser.parse_args()

    unknown = [c for c in args.competitions if c not in INTERNATIONAL_COMPETITIONS]
    if unknown:
        parser.error(f"Unknown competition(s): {unknown}")

    print(f"Fetching {CSV_URL} ...")
    rows = fetch_csv_rows(CSV_URL)
    print(f"Fetched {len(rows)} total international matches (all competitions/eras).")

    conn = sqlite3.connect(args.db)
    conn.execute("PRAGMA foreign_keys = ON")

    counts = load(conn, rows, args.competitions)
    conn.close()

    print("\n=== Loaded/updated per competition ===")
    total = 0
    for key, count in counts.items():
        print(f"  {INTERNATIONAL_COMPETITIONS[key]['name']:45s} {count:5d}")
        total += count
    print(f"\nDone. {total} total matches loaded/updated.")


if __name__ == "__main__":
    main()
