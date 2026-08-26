"""
Loads *upcoming* fixtures (not yet played) for the next N days from
SofaScore's date-based schedule endpoint — one call per day, globally
across every league SofaScore covers — for the "today's fixtures" feature
on /simulate (pick a real fixture instead of typing two team names).

Deliberately different from sofascore_scraper.py's approach: that script
is scoped to one tournament_id at a time (backfilling full season
history). This one is scoped to a *date* and spans every competition
SofaScore has scheduled that day, so it can't assume which league an
event belongs to ahead of time.

Because of that, this script never creates a new team. It only stores a
fixture if BOTH teams already exist in our `teams` table (i.e. we've
already scraped their history from somewhere and have a rating for
them) — a fixture between two teams we've never seen has nothing to
simulate with anyway, so there's no upside to guessing at a name match
and real downside (a name-spelling mismatch would silently create a
duplicate "shadow" team with zero rating history). Skipped fixtures are
counted and logged, not silently dropped.

BROKEN — confirmed 2026-08-25: /sport/football/scheduled-events/{date}
is not a real SofaScore endpoint (clean 404, not a block — checked the
raw response body directly). Sniffed the actual network calls
www.sofascore.com's own football page makes for a given date and it's a
two-step fetch instead: GET
/sport/football/scheduled-tournaments/{date}/page/{n} to find which
tournaments have anything on that date, then GET
/unique-tournament/{id}/scheduled-events/{date} per tournament — no
single flat "everything today" call exists. That's a much heavier,
unbounded-per-day request shape (one call per active tournament,
globally) than this project wants to lean on, especially against a
source that's already shown it'll circuit-break/block on request
volume — so results-grading was built into sofascore_weekly_fixtures.py
instead (adds an events/last/{page} pass alongside its existing
events/next/{page} one, scoped to the already-curated league config,
same proven request shape). Left this file's original code below
un-run rather than half-migrated, in case the two-step approach above
is ever worth revisiting for broader coverage.

Per the project's standing rule, this runs locally, never on the
production server — see sofascore_scraper.py's own docstring for why.

Usage:
    python sofascore_daily_fixtures.py --db data/football.db --days 3
"""

import argparse
import sqlite3
import sys
import time
from datetime import UTC, datetime, timedelta

sys.path.insert(0, ".")
from sofascore_scraper import (  # noqa: E402
    BASE_URL,
    SOURCE,
    BlockedError,
    _get,
    get_or_create_league,
    get_or_create_season,
    parse_year_start,
)

REQUEST_DELAY_SECONDS = 0.6


def _ensure_kickoff_column(conn: sqlite3.Connection) -> None:
    cols = {row[1] for row in conn.execute("PRAGMA table_info(matches)")}
    if "kickoff_utc" not in cols:
        conn.execute("ALTER TABLE matches ADD COLUMN kickoff_utc INTEGER")
        conn.commit()
        print("  (migrated: added matches.kickoff_utc)")


def fetch_events_for_date(date_str: str) -> list[dict]:
    data = _get(f"{BASE_URL}/sport/football/scheduled-events/{date_str}")
    time.sleep(REQUEST_DELAY_SECONDS)
    if not data:
        return []
    return data.get("events", [])


def find_existing_team(conn, sofascore_id: int, name: str, country: str) -> int | None:
    """Same lookup order as sofascore_scraper.get_or_create_team, but never
    creates a new team — a miss here means "skip this fixture", not
    "invent a team with no history to simulate from"."""
    alias = str(sofascore_id)
    row = conn.execute(
        "SELECT team_id FROM team_aliases WHERE source = ? AND alias = ?", (SOURCE, alias)
    ).fetchone()
    if row:
        return row[0]

    row = conn.execute(
        "SELECT id FROM teams WHERE name = ? AND country = ?", (name, country)
    ).fetchone()
    if row is None:
        return None
    team_id = row[0]
    # Backfill the alias so this same team resolves by id next run, even
    # if its stored name/country ever drifts from what SofaScore sends.
    conn.execute(
        "INSERT OR IGNORE INTO team_aliases (team_id, source, alias) VALUES (?, ?, ?)",
        (team_id, SOURCE, alias),
    )
    return team_id


_printed_sample = False


def insert_scheduled_event(conn, event: dict) -> str:
    """Returns 'inserted', 'skipped_unknown_team', or 'skipped_error'."""
    global _printed_sample
    if not _printed_sample:
        print(f"  (sample event keys: {sorted(event.keys())})")
        _printed_sample = True

    try:
        home = event["homeTeam"]
        away = event["awayTeam"]
        home_id = find_existing_team(
            conn, home["id"], home["name"], home.get("country", {}).get("name") or ""
        )
        away_id = find_existing_team(
            conn, away["id"], away["name"], away.get("country", {}).get("name") or ""
        )
        if home_id is None or away_id is None:
            return "skipped_unknown_team"
        conn.commit()  # release any new-alias write before more queries

        tournament = event.get("tournament", {})
        league_name = tournament.get("name")
        league_country = tournament.get("category", {}).get("name")
        if not league_name:
            return "skipped_error"
        league_id = get_or_create_league(conn, league_name, league_country, "club")
        conn.commit()

        season_info = event.get("season", {})
        year_start = (
            parse_year_start(season_info["year"]) if season_info.get("year")
            else datetime.now(UTC).year
        )
        season_id = get_or_create_season(conn, league_id, year_start)

        kickoff_ts = event["startTimestamp"]
        match_date = datetime.fromtimestamp(kickoff_ts, tz=UTC).strftime("%Y-%m-%d")
        source_match_id = str(event["id"])

        conn.execute(
            """
            INSERT INTO matches (
                league_id, season_id, match_date, kickoff_utc,
                home_team_id, away_team_id, status,
                is_neutral_venue, source, source_match_id
            ) VALUES (?, ?, ?, ?, ?, ?, 'scheduled', 0, ?, ?)
            ON CONFLICT(source, source_match_id) DO UPDATE SET
                kickoff_utc=excluded.kickoff_utc,
                match_date=excluded.match_date
            """,
            (league_id, season_id, match_date, kickoff_ts,
             home_id, away_id, SOURCE, source_match_id),
        )
        conn.commit()
        return "inserted"
    except Exception as exc:
        print(f"    skipping malformed event ({exc}): {event.get('id', '?')}")
        return "skipped_error"


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Load the next N days of upcoming fixtures from SofaScore, "
        "for teams we already track, into football.db"
    )
    parser.add_argument("--db", default="data/football.db")
    parser.add_argument("--days", type=int, default=3, help="How many days ahead, including today")
    args = parser.parse_args()

    conn = sqlite3.connect(args.db, timeout=30)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    _ensure_kickoff_column(conn)

    totals = {"inserted": 0, "skipped_unknown_team": 0, "skipped_error": 0}
    today = datetime.now(UTC).date()

    try:
        for offset in range(args.days):
            date_str = (today + timedelta(days=offset)).isoformat()
            print(f"\n=== {date_str} ===")
            events = fetch_events_for_date(date_str)
            print(f"  {len(events)} event(s) returned")
            for event in events:
                result = insert_scheduled_event(conn, event)
                totals[result] += 1
            print(f"  -> {totals['inserted']} inserted so far, "
                  f"{totals['skipped_unknown_team']} skipped (unknown team), "
                  f"{totals['skipped_error']} skipped (error)")
    except BlockedError as exc:
        conn.close()
        print(f"\nABORTED — {exc}")
        print(f"Totals before aborting: {totals}")
        raise SystemExit(2) from None

    conn.close()
    print(f"\nDone. {totals['inserted']} fixture(s) inserted/updated, "
          f"{totals['skipped_unknown_team']} skipped for unrecognized teams, "
          f"{totals['skipped_error']} skipped for parse errors, "
          f"across {args.days} day(s).")


if __name__ == "__main__":
    main()
