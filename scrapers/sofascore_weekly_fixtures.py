"""
Lightweight refresh of *upcoming and recently-finished* fixtures for
every SofaScore-tracked league (see leagues_config.SOFASCORE_LEAGUES /
WEEKLY_FIXTURES_LEAGUES). Powers two features from the one pass over
each tournament's events:

  1. The Monday-Sunday fixture picker on /simulate (see
     _load_upcoming_fixtures in webapp/routes/customer.py) — from the
     upcoming (events/next) side.
  2. Grading the /accuracy track record — a prediction stays "Pending"
     until some row in `matches` shows status='finished' for that team
     pair/date (see webapp/accuracy.py's _actual_result, which matches
     by team name + date window, not by source — a finished row landed
     here satisfies it exactly the same as one from
     api_football_daily_fixtures.py). This is now the primary result
     source, specifically because it isn't capped to "yesterday" the
     way that script's free-tier API-Football lookback is, and covers
     whatever's in this file's league config rather than only whatever
     already has a matching `leagues` row by name/country.

Deliberately NOT a re-run of sofascore_scraper.py's full backfill (which
walks a league's entire round structure — many requests per league).
Instead this hits SofaScore's GET
.../unique-tournament/{id}/season/{id}/events/next/{page} AND
.../events/last/{page} endpoints — confirmed directly against the live
API: one page returns 30 events, more than enough to cover a week
either direction for any single competition. Two requests per
*distinct* (tournament_id, season_id) pair per run — several configured
leagues share a tournament_id (e.g. UEFA Europa League / UEFA Europa
League Qualification both key off tournament_id 679), so each raw
response is fetched once and reused for every config that references
it, via event_passes_filter, rather than re-fetched per league.

Per the project's standing rule, this runs locally, never on the
production server — see sofascore_scraper.py's own docstring for why.
An earlier version of this docstring claimed a carve-out to run on the
server (matching api_football_daily_fixtures.py's daily_fixtures_sync.sh
cron); that was reverted 2026-08-25 after confirming SofaScore 403s the
EC2 IP immediately, on the very first request, independent of how
infrequently this runs — not a request-volume problem, so there's no
safe frequency to fall back to on that box.

Usage:
    python sofascore_weekly_fixtures.py --db data/football.db
"""

import argparse
import sqlite3
from datetime import UTC, datetime

from leagues_config import SOFASCORE_LEAGUES, WEEKLY_FIXTURES_LEAGUES
from sofascore_scraper import (
    BASE_URL,
    SOURCE,
    BlockedError,
    _get,
    event_passes_filter,
    get_or_create_team,
)

PAGES_PER_TOURNAMENT = 1  # 30 events/page — enough for a week, per league, per direction


def _current_season_row(conn: sqlite3.Connection, league_id: int) -> int | None:
    """None means this league has no historical backfill yet (see
    SOFASCORE_LEAGUES — it holds every league on the eventual roadmap,
    most not yet backfilled). Skipped, not an error: this script picks
    a league up automatically the moment its backfill lands, with no
    code change needed here."""
    row = conn.execute(
        "SELECT id FROM seasons WHERE league_id = ? ORDER BY year_start DESC LIMIT 1",
        (league_id,),
    ).fetchone()
    return row[0] if row else None


def insert_upcoming_event(conn, league_id: int, season_row_id: int, event: dict) -> bool:
    """Returns whether the event was recorded as finished — callers use
    this just for the finished/upcoming split in their own totals."""
    home = event["homeTeam"]
    away = event["awayTeam"]
    home_id = get_or_create_team(conn, home["id"], home["name"], home.get("country", {}).get("name") or "")
    away_id = get_or_create_team(conn, away["id"], away["name"], away.get("country", {}).get("name") or "")
    conn.commit()

    kickoff_ts = event["startTimestamp"]
    match_date = datetime.fromtimestamp(kickoff_ts, tz=UTC).strftime("%Y-%m-%d")
    source_match_id = str(event["id"])
    is_finished = event.get("status", {}).get("type") == "finished"
    status = "finished" if is_finished else "scheduled"
    home_goals = event.get("homeScore", {}).get("current") if is_finished else None
    away_goals = event.get("awayScore", {}).get("current") if is_finished else None

    conn.execute(
        """
        INSERT INTO matches (
            league_id, season_id, match_date, kickoff_utc,
            home_team_id, away_team_id, home_goals, away_goals, status,
            is_neutral_venue, source, source_match_id
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?)
        ON CONFLICT(source, source_match_id) DO UPDATE SET
            kickoff_utc=excluded.kickoff_utc,
            match_date=excluded.match_date,
            home_goals=excluded.home_goals,
            away_goals=excluded.away_goals,
            status=excluded.status
        """,
        (league_id, season_row_id, match_date, kickoff_ts, home_id, away_id,
         home_goals, away_goals, status, SOURCE, source_match_id),
    )
    conn.commit()
    return is_finished


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Refresh upcoming fixtures and recent results for every "
        "SofaScore-tracked league"
    )
    parser.add_argument("--db", default="data/football.db")
    args = parser.parse_args()

    conn = sqlite3.connect(args.db, timeout=30)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")

    # Cache the raw fetch per (tournament_id, season_id) so leagues that
    # share one (e.g. the Europa/Conference League main-comp + qualifier
    # pairs) only cost two requests (next + last), not four.
    raw_cache: dict[tuple[int, int], list[dict]] = {}
    totals = {"inserted": 0, "skipped_unknown_team": 0, "finished": 0, "upcoming": 0}

    # SOFASCORE_LEAGUES entries keep a multi-season history array (for
    # sofascore_scraper.py's full backfill) — only the latest is relevant
    # here. WEEKLY_FIXTURES_LEAGUES entries only ever carry the current
    # season, since that's the only thing this script needs from them.
    all_leagues = [
        (key, cfg, cfg["season_ids"][-1]) for key, cfg in SOFASCORE_LEAGUES.items()
    ] + [
        (key, cfg, cfg["season_id"]) for key, cfg in WEEKLY_FIXTURES_LEAGUES.items()
    ]

    try:
        for key, cfg, season_id in all_leagues:
            # Look up only — never create. Most of SOFASCORE_LEAGUES is the
            # eventual roadmap, not what's backfilled yet (see
            # _current_season_row); creating a league row here for one
            # that has no data at all would be the exact same
            # empty-shell-league bug already found and fixed once today.
            row = conn.execute(
                "SELECT id FROM leagues WHERE name = ? AND country IS ?",
                (cfg["name"], cfg["country"]),
            ).fetchone()
            league_id = row[0] if row else None
            season_row_id = _current_season_row(conn, league_id) if league_id else None
            if season_row_id is None:
                print(f"  [{cfg['name']}] skipped — not backfilled yet")
                continue

            tournament_id = cfg["tournament_id"]
            cache_key = (tournament_id, season_id)

            if cache_key not in raw_cache:
                events: list[dict] = []
                for direction in ("next", "last"):
                    for page in range(PAGES_PER_TOURNAMENT):
                        data = _get(f"{BASE_URL}/unique-tournament/{tournament_id}/season/{season_id}/events/{direction}/{page}")
                        if not data:
                            break
                        events.extend(data.get("events", []))
                        if not data.get("hasNextPage"):
                            break
                raw_cache[cache_key] = events

            events = [e for e in raw_cache[cache_key] if event_passes_filter(e, cfg)]

            inserted = finished = 0
            for event in events:
                is_finished = insert_upcoming_event(conn, league_id, season_row_id, event)
                inserted += 1
                finished += is_finished
            totals["inserted"] += inserted
            totals["finished"] += finished
            totals["upcoming"] += inserted - finished
            print(f"  [{cfg['name']}] {inserted} fixture(s) loaded/updated "
                  f"({finished} finished, {inserted - finished} upcoming)")
    except BlockedError as exc:
        conn.close()
        print(f"\nABORTED — {exc}")
        print(f"{totals['inserted']} fixture(s) loaded/updated before aborting "
              f"({totals['finished']} finished, {totals['upcoming']} upcoming).")
        raise SystemExit(2) from None

    conn.close()
    print(f"\nDone. {totals['inserted']} fixture(s) loaded/updated "
          f"({totals['finished']} finished, {totals['upcoming']} upcoming) "
          f"across {len(all_leagues)} configured league(s) "
          f"({len(raw_cache)} distinct tournament/season pair(s), "
          f"{len(raw_cache) * 2} request(s)).")


if __name__ == "__main__":
    main()
