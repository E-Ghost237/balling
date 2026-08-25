"""
Lightweight refresh of *upcoming* fixtures for every SofaScore-tracked
league (see leagues_config.SOFASCORE_LEAGUES) — powers the Monday-Sunday
fixture picker on /simulate (see _load_upcoming_fixtures in
webapp/routes/customer.py).

Deliberately NOT a re-run of sofascore_scraper.py's full backfill (which
walks a league's entire round structure — many requests per league).
Instead this hits SofaScore's GET
.../unique-tournament/{id}/season/{id}/events/next/{page} endpoint —
confirmed directly against the live API: one page returns the next 30
upcoming events for a tournament/season, more than enough to cover a
week for any single competition. One request per *distinct*
(tournament_id, season_id) pair per run — several configured leagues
share a tournament_id (e.g. UEFA Europa League / UEFA Europa League
Qualification both key off tournament_id 679), so the raw response is
fetched once and reused for each config that references it, via
event_passes_filter, rather than fetched twice.

Per the project's usual rule this would run locally only, but per
explicit instruction this is the one SofaScore job allowed on the
production server — same carve-out already made for
api_football_daily_fixtures.py — specifically because it's this cheap
(one request per unique tournament/season, run once a day), not a
license to run it more often or widen it without re-checking that math.

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

PAGES_PER_TOURNAMENT = 1  # 30 events/page — enough for a week, per league


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


def insert_upcoming_event(conn, league_id: int, season_row_id: int, event: dict) -> None:
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


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Refresh upcoming fixtures for every SofaScore-tracked league"
    )
    parser.add_argument("--db", default="data/football.db")
    args = parser.parse_args()

    conn = sqlite3.connect(args.db, timeout=30)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")

    # Cache the raw fetch per (tournament_id, season_id) so leagues that
    # share one (e.g. the Europa/Conference League main-comp + qualifier
    # pairs) only cost one request, not two.
    raw_cache: dict[tuple[int, int], list[dict]] = {}
    totals = {"inserted": 0, "skipped_unknown_team": 0}

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
                for page in range(PAGES_PER_TOURNAMENT):
                    data = _get(f"{BASE_URL}/unique-tournament/{tournament_id}/season/{season_id}/events/next/{page}")
                    if not data:
                        break
                    events.extend(data.get("events", []))
                    if not data.get("hasNextPage"):
                        break
                raw_cache[cache_key] = events

            events = [e for e in raw_cache[cache_key] if event_passes_filter(e, cfg)]

            inserted = 0
            for event in events:
                insert_upcoming_event(conn, league_id, season_row_id, event)
                inserted += 1
            totals["inserted"] += inserted
            print(f"  [{cfg['name']}] {inserted} upcoming fixture(s) loaded/updated")
    except BlockedError as exc:
        conn.close()
        print(f"\nABORTED — {exc}")
        print(f"{totals['inserted']} fixture(s) loaded/updated before aborting.")
        raise SystemExit(2) from None

    conn.close()
    print(f"\nDone. {totals['inserted']} upcoming fixture(s) loaded/updated "
          f"across {len(all_leagues)} configured league(s) "
          f"({len(raw_cache)} distinct tournament/season request(s)).")


if __name__ == "__main__":
    main()
