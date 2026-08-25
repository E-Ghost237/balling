"""
Loads *upcoming* fixtures (not yet played) for the next N days from
API-Football (api-sports.io) — replaces the earlier SofaScore-based and
football-data.org-based plans for this specific feature: API-Football's
free tier covers 1,200+ leagues (verified: a single date-only call
returned 754 fixtures spanning leagues on every continent) with a 100
requests/day cap, which a 3-day daily pull (3 requests/day) stays nowhere
near.

Verified directly against the live API before writing this (see chat —
not guessed at): GET /fixtures?date=YYYY-MM-DD with no league filter
returns every fixture scheduled that day across every competition, in
one call. Confirmed field shape for both finished ("FT") and upcoming
("NS" = Not Started) fixtures — status.short, fixture.timestamp (Unix,
used directly, no conversion needed), teams.home/away.id+name,
league.id+name+country, goals.home/away (null until played).

Also verified directly (not documented anywhere obvious): the free tier's
date access is a strict 3-day rolling window — yesterday, today, and
tomorrow only. A request for two days back or two days forward returns a
clean {"errors": {"plan": "..."}} response, zero fixtures, no quota
charged. --days/--lookback-days default to what's actually inside that
window (2 and 1) rather than what would be nice to have; asking for more
just burns a request on a guaranteed-empty response.

Same safety principle as sofascore_daily_fixtures.py: this never creates
a new team, and — as of 2026-08-24 — never creates a new league either.
A fixture is only stored if its league already exists in our `leagues`
table (i.e. one we deliberately track and have ratings history for) AND
both teams already exist in our `teams` table. The league check matters
just as much as the team check: API-Football returns every competition
worldwide with no way to filter server-side, and plenty of clubs run a
reserve/youth/women's side under the same or near-identical name as the
first team we actually track (confirmed live: Italy's U19 "Primavera",
Germany's "Frauen Bundesliga", England's U21 "Premier League 2" all
slipped in as brand-new, ratings-less leagues before this check existed
— see LEAGUE_NAME_ALIASES / get_tracked_league_id). Untracked-league and
unrecognized-team fixtures are both skipped and counted, not silently
dropped without a trace.

This is the one piece of fetch/scrape work that runs on the production
server rather than locally (see daily_fixtures_sync.sh) — an explicit,
deliberate exception to the project's usual "scraping stays off the
production box" rule, since this is a handful of lightweight, rate
limited API calls rather than scraping traffic.

Usage:
    export API_FOOTBALL_KEY=your_key_here
    python api_football_daily_fixtures.py --db data/football.db --days 3
"""

import argparse
import os
import sqlite3
import time
from datetime import UTC, datetime, timedelta

import requests

API_BASE = "https://v3.football.api-sports.io"
SOURCE = "api-football"

# Confirmed live: 10 req/min, 100 req/day on the free tier. A day's worth
# of this script's own usage (--days, default 3) never gets remotely
# close to either — this delay just keeps us comfortably under the
# per-minute cap in case the daily job is ever widened later.
REQUEST_DELAY_SECONDS = 1.0


def _ensure_kickoff_column(conn: sqlite3.Connection) -> None:
    cols = {row[1] for row in conn.execute("PRAGMA table_info(matches)")}
    if "kickoff_utc" not in cols:
        conn.execute("ALTER TABLE matches ADD COLUMN kickoff_utc INTEGER")
        conn.commit()
        print("  (migrated: added matches.kickoff_utc)")


def fetch_fixtures_for_date(api_key: str, date_str: str) -> list[dict]:
    resp = requests.get(
        f"{API_BASE}/fixtures",
        params={"date": date_str},
        headers={"x-apisports-key": api_key},
        timeout=20,
    )
    remaining_day = resp.headers.get("x-ratelimit-requests-remaining")
    if remaining_day is not None:
        print(f"  (API-Football quota remaining today: {remaining_day})")

    if resp.status_code != 200:
        print(f"  [{resp.status_code}] {resp.text[:200]}")
        return []

    data = resp.json()
    if data.get("errors"):
        print(f"  API reported errors: {data['errors']}")
        return []
    return data.get("response", [])


# API-Football names a league by its current sponsor/formal name, which
# doesn't always match the name our historical data (scraped elsewhere)
# already uses for the same competition — without this, a perfectly
# recognized league gets a brand-new, ratings-less duplicate row instead
# of reusing the one with years of history. Add to this as mismatches
# turn up; confirmed live against the API on 2026-08-24.
LEAGUE_NAME_ALIASES = {
    "Jupiler Pro League": "Belgian Pro League",
}
# Country-scoped: API-Football's "Premier League" (country=Russia) is our
# "Russian Premier League" — the plain "Premier League" name is already
# correctly used elsewhere (England), so this can't be a name-only alias.
LEAGUE_NAME_COUNTRY_ALIASES = {
    ("Premier League", "Russia"): "Russian Premier League",
}


def get_tracked_league_id(conn, name: str, country: str | None) -> int | None:
    """Looks up a league — never creates one. API-Football returns every
    competition worldwide with no way to filter server-side (see module
    docstring), so this is the actual scope boundary: only a fixture whose
    league already exists in our `leagues` table (i.e. one we deliberately
    track and have historical data/ratings for) is eligible for insertion.
    Without this gate, team-name matching alone would happily pull in a
    club's reserve/youth/women's side under an identical or near-identical
    name (confirmed live: Italy's U19 "Primavera" league, Germany's
    "Frauen Bundesliga", England's U21 "Premier League 2" all slipped in
    this way on 2026-08-24) and auto-create a brand-new, ratings-less
    league row for it."""
    name = LEAGUE_NAME_COUNTRY_ALIASES.get((name, country), LEAGUE_NAME_ALIASES.get(name, name))
    row = conn.execute(
        "SELECT id FROM leagues WHERE name = ? AND country IS ?", (name, country)
    ).fetchone()
    return row[0] if row else None


def get_or_create_season(conn, league_id: int, year_start: int) -> int:
    row = conn.execute(
        "SELECT id FROM seasons WHERE league_id = ? AND year_start = ?", (league_id, year_start)
    ).fetchone()
    if row:
        return row[0]
    cur = conn.execute(
        "INSERT INTO seasons (league_id, year_start) VALUES (?, ?)", (league_id, year_start)
    )
    return cur.lastrowid


def find_existing_team(conn, api_football_id: int, name: str) -> int | None:
    """Only ever resolves to a team we already have rating history for —
    never creates one. API-Football doesn't hand us a country per team in
    the fixtures payload (only per-league), so unlike the SofaScore
    version this matches by name alone once the alias lookup misses —
    slightly higher chance of a same-name false match across countries
    for very generic club names, accepted as a v1 tradeoff (worst case:
    a fixture resolves to the wrong same-named team and produces a
    prediction that's simply wrong for that one fixture, not a crash)."""
    alias = str(api_football_id)
    row = conn.execute(
        "SELECT team_id FROM team_aliases WHERE source = ? AND alias = ?", (SOURCE, alias)
    ).fetchone()
    if row:
        return row[0]

    row = conn.execute("SELECT id FROM teams WHERE name = ?", (name,)).fetchone()
    if row is None:
        # Some clubs carry a formal/sponsor prefix or suffix in
        # API-Football's naming that our own team name doesn't (e.g.
        # "Club Brugge KV" / "KVC Westerlo" vs our "Club Brugge" /
        # "Westerlo") — fall back to a substring match in either
        # direction. Length-gated and only accepted when it resolves to
        # exactly one candidate, so a short/generic name never risks a
        # wrong match across unrelated clubs.
        candidates = conn.execute(
            "SELECT id FROM teams WHERE length(name) >= 5 "
            "AND (? LIKE '%' || name || '%' OR name LIKE '%' || ? || '%')",
            (name, name),
        ).fetchall()
        if len(candidates) == 1:
            row = candidates[0]
    if row is None:
        return None
    team_id = row[0]
    conn.execute(
        "INSERT OR IGNORE INTO team_aliases (team_id, source, alias) VALUES (?, ?, ?)",
        (team_id, SOURCE, alias),
    )
    return team_id


_printed_sample = False


def insert_fixture(conn, fixture: dict) -> str:
    """Returns 'inserted', 'skipped_untracked_league', 'skipped_unknown_team',
    or 'skipped_error'."""
    global _printed_sample
    if not _printed_sample:
        print(f"  (sample fixture top-level keys: {sorted(fixture.keys())})")
        _printed_sample = True

    try:
        league = fixture.get("league", {})
        league_name = league.get("name")
        league_country = league.get("country")
        if not league_name:
            return "skipped_error"

        # League check comes before team matching, not just after: it's
        # also what keeps the team-name fallback's blast radius small — a
        # fixture from a league we don't track never reaches the
        # substring-matching step at all, so it can't produce a
        # cross-country false-positive team match for a competition we
        # were never going to keep anyway.
        league_id = get_tracked_league_id(conn, league_name, league_country)
        if league_id is None:
            return "skipped_untracked_league"

        home = fixture["teams"]["home"]
        away = fixture["teams"]["away"]
        home_id = find_existing_team(conn, home["id"], home["name"])
        away_id = find_existing_team(conn, away["id"], away["name"])
        if home_id is None or away_id is None:
            return "skipped_unknown_team"
        conn.commit()

        year_start = league.get("season") or datetime.now(UTC).year
        season_id = get_or_create_season(conn, league_id, int(year_start))

        info = fixture["fixture"]
        kickoff_ts = info["timestamp"]
        match_date = datetime.fromtimestamp(kickoff_ts, tz=UTC).strftime("%Y-%m-%d")
        source_match_id = str(info["id"])

        is_finished = info.get("status", {}).get("short") in ("FT", "AET", "PEN")
        goals = fixture.get("goals", {})
        home_goals = goals.get("home") if is_finished else None
        away_goals = goals.get("away") if is_finished else None
        status = "finished" if is_finished else "scheduled"

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
            (league_id, season_id, match_date, kickoff_ts,
             home_id, away_id, home_goals, away_goals, status, SOURCE, source_match_id),
        )
        conn.commit()
        return "inserted"
    except Exception as exc:
        print(f"    skipping malformed fixture ({exc}): {fixture.get('fixture', {}).get('id', '?')}")
        return "skipped_error"


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Load the next N days of upcoming fixtures from API-Football, "
        "for teams we already track, into football.db"
    )
    parser.add_argument(
        "--db", default="data/football.db"
    )
    parser.add_argument(
        "--days", type=int, default=2,
        help="How many days ahead, including today (free-tier window only reaches tomorrow)",
    )
    parser.add_argument(
        "--lookback-days", type=int, default=1,
        help="How many days *before* today to re-fetch (free-tier window only reaches "
        "yesterday), so a match that was 'scheduled' when first inserted gets upserted to "
        "'finished' with its real score once it's over — nothing else ever re-visits a "
        "past date, so without this every graded prediction in the accuracy log would sit "
        "on 'pending' forever.",
    )
    parser.add_argument("--api-key", default=os.environ.get("API_FOOTBALL_KEY"))
    args = parser.parse_args()

    if not args.api_key:
        print("No API key provided. Pass --api-key YOUR_KEY or set API_FOOTBALL_KEY.")
        return

    conn = sqlite3.connect(args.db, timeout=30)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    _ensure_kickoff_column(conn)

    totals = {
        "inserted": 0, "skipped_untracked_league": 0,
        "skipped_unknown_team": 0, "skipped_error": 0,
    }
    today = datetime.now(UTC).date()

    for offset in range(-args.lookback_days, args.days):
        date_str = (today + timedelta(days=offset)).isoformat()
        print(f"\n=== {date_str} ===")
        fixtures = fetch_fixtures_for_date(args.api_key, date_str)
        print(f"  {len(fixtures)} fixture(s) returned")
        for fixture in fixtures:
            result = insert_fixture(conn, fixture)
            totals[result] += 1
        time.sleep(REQUEST_DELAY_SECONDS)
        print(f"  -> {totals['inserted']} inserted so far, "
              f"{totals['skipped_untracked_league']} skipped (untracked league), "
              f"{totals['skipped_unknown_team']} skipped (unknown team), "
              f"{totals['skipped_error']} skipped (error)")

    conn.close()
    print(f"\nDone. {totals['inserted']} fixture(s) inserted/updated, "
          f"{totals['skipped_untracked_league']} skipped for untracked leagues, "
          f"{totals['skipped_unknown_team']} skipped for unrecognized teams, "
          f"{totals['skipped_error']} skipped for parse errors, "
          f"across {args.days} day(s).")


if __name__ == "__main__":
    main()
