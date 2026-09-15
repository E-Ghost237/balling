"""
Fixes a real data bug found live 2026-09-15: several of a team's *scheduled*
SofaScore-sourced fixtures sharing the exact same kickoff_utc as another of
that team's fixtures — impossible in reality (a team can't play two
different opponents at once). Confirmed by hand for AC Milan's UEFA Europa
League fixtures: rounds 3-7 all carried round 1's timestamp.

Root cause: SofaScore assigns a placeholder kickoff time to a fixture whose
real broadcast slot isn't confirmed yet (typical for a fixture many weeks
out), then corrects it once confirmed. Our own fetch only ever pulls the
*next 30 events for the whole tournament* (sofascore_weekly_fixtures.py,
PAGES_PER_TOURNAMENT=1) — for a big competition (Europa/Conference League's
36-team league phase, 8 rounds each) a team's later rounds fall outside that
window and never get re-fetched, so whatever placeholder they got on first
sight is what sits in the db indefinitely, even after SofaScore itself has
since corrected it.

This finds every such collision (same team, same scheduled kickoff_utc as
another of that team's own scheduled matches — a real fixture never
legitimately shares its exact kickoff with another of the same team's
fixtures) and re-fetches each one individually via GET /event/{id}, which
returns SofaScore's current data regardless of tournament-wide pagination
windows — confirmed live: this returns the real, distinct, corrected
timestamp for exactly the events affected here.

Not a one-off cleanup — new far-out fixtures get inserted with a
placeholder the same way on every fetch, so this is meant to be re-run
periodically (or wired into the routine fetch) alongside dedupe_teams.py.

Usage:
    python fix_placeholder_kickoffs.py --db data/football.db
"""

import argparse
import sqlite3
from datetime import UTC, datetime

from sofascore_scraper import BASE_URL, BlockedError, _get

REQUEST_DELAY_SECONDS = 0.6


def find_collisions(conn: sqlite3.Connection) -> list[tuple[int, str]]:
    """Returns (match_id, source_match_id) for every scheduled SofaScore
    match whose kickoff_utc collides with another scheduled match
    involving the same team (home or away, either side)."""
    return conn.execute(
        """
        SELECT m.id, m.source_match_id
        FROM matches m
        WHERE m.status = 'scheduled' AND m.source = 'sofascore' AND m.kickoff_utc IN (
            SELECT kickoff_utc FROM (
                SELECT home_team_id AS team_id, kickoff_utc FROM matches
                WHERE status = 'scheduled' AND source = 'sofascore' AND kickoff_utc IS NOT NULL
                UNION ALL
                SELECT away_team_id AS team_id, kickoff_utc FROM matches
                WHERE status = 'scheduled' AND source = 'sofascore' AND kickoff_utc IS NOT NULL
            )
            GROUP BY team_id, kickoff_utc
            HAVING COUNT(*) > 1
        )
        """
    ).fetchall()


def fix_kickoffs(conn: sqlite3.Connection) -> dict[str, int]:
    """Re-fetches each colliding match's real event data and corrects its
    kickoff_utc/match_date. Returns a totals dict."""
    totals = {"fixed": 0, "unchanged": 0, "fetch_failed": 0}
    try:
        for match_id, source_match_id in find_collisions(conn):
            data = _get(f"{BASE_URL}/event/{source_match_id}")
            if not data:
                totals["fetch_failed"] += 1
                continue
            event = data.get("event", {})
            new_ts = event.get("startTimestamp")
            if not new_ts:
                totals["fetch_failed"] += 1
                continue
            row = conn.execute(
                "SELECT kickoff_utc FROM matches WHERE id = ?", (match_id,)
            ).fetchone()
            if row and row[0] == new_ts:
                totals["unchanged"] += 1
            else:
                new_date = datetime.fromtimestamp(new_ts, tz=UTC).strftime("%Y-%m-%d")
                conn.execute(
                    "UPDATE matches SET kickoff_utc = ?, match_date = ? WHERE id = ?",
                    (new_ts, new_date, match_id),
                )
                conn.commit()
                totals["fixed"] += 1
            import time
            time.sleep(REQUEST_DELAY_SECONDS)
    except BlockedError as exc:
        print(f"\nABORTED — {exc}")
        print(f"Totals before aborting: {totals}")
        raise
    return totals


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Fix placeholder kickoff times on scheduled SofaScore fixtures"
    )
    parser.add_argument("--db", default="data/football.db")
    args = parser.parse_args()

    conn = sqlite3.connect(args.db, timeout=30)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")

    collisions = find_collisions(conn)
    print(f"Found {len(collisions)} match(es) with a colliding placeholder kickoff.")

    try:
        totals = fix_kickoffs(conn)
    except BlockedError:
        conn.close()
        raise SystemExit(2) from None

    conn.close()
    print(f"\nDone. {totals['fixed']} fixed, {totals['unchanged']} already correct, "
          f"{totals['fetch_failed']} failed to fetch.")


if __name__ == "__main__":
    main()
