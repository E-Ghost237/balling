"""
Loads league/season/team/match data from api.sofascore.com's undocumented
internal API, for leagues no other source in this project covers at all
(see scrapers/leagues_config.SOFASCORE_LEAGUES — Iran, Iraq, and the UEFA
Champions/Europa/Conference League main competitions + their qualifying
rounds, the latter split out via each event's own tournament name — see
event_passes_filter()).

Deliberately minimal: only fixtures/results (team names, scores, dates),
no shot-level data, no xG. home_xg/away_xg stay NULL — same
'goals_only_fallback' mode already used for the football-data.co.uk
domestic leagues and the international-results loader.

Per the project's standing rule, this script is meant to be run locally
(never on the production server) — see conversation notes. Its output
(an updated football.db) gets transferred to the server separately.

Team logos and country are set directly from SofaScore's own data at
insert time — team.id and team.country are both given to us on every
event response, so there's no name-matching/guessing step (the exact
failure mode that mistagged teams via TheSportsDB in the past — see
db/fix_team_countries.py and scrapers/thesportsdb_enrich.py). The logo
URL (https://api.sofascore.com/api/v1/team/{sofascore_id}/image) is
keyed by that same numeric ID, so it can never attach the wrong badge.

Requires cloudscraper (`pip install cloudscraper`), not plain requests — plain
`requests`/curl get a flat 403 from every single call here regardless of
headers (verified directly), which is Cloudflare's TLS/JS-challenge
fingerprinting rejecting a non-browser client, not a simple User-Agent
check. cloudscraper mimics a real browser's TLS handshake and solves that
challenge automatically.

Usage:
    python sofascore_scraper.py --db data/football.db
    python sofascore_scraper.py --db data/football.db --leagues Persian_Gulf_Pro_League
"""

import argparse
import sqlite3
import time
from datetime import UTC, datetime

import cloudscraper

from leagues_config import SOFASCORE_LEAGUES

BASE_URL = "https://api.sofascore.com/api/v1"
SOURCE = "sofascore"
REQUEST_DELAY_SECONDS = 0.6

# Circuit breaker: once we're actually blocked, EVERY subsequent request
# exhausts its own 5-attempt retry ladder (2+4+6+8+10=30s of backoff each)
# before giving up and moving to the next event — and there can be
# hundreds of events left in a season/league list. Measured directly: two
# workers blocked mid-run each logged 70+ fully-exhausted-retry failures,
# which is 70+ x 30s+ = 35+ minutes of guaranteed-to-fail grinding *per
# worker*, on top of however long it takes to notice the run never
# finished. Aborting the whole process after a short run of consecutive
# failures turns "blocked for the rest of the run" into "blocked, abort
# in under a minute" — the difference between a quick, informative failure
# and burning a large chunk of the (already-tightening, see project
# memory) block window on requests that were never going to succeed.
CONSECUTIVE_FAILURE_LIMIT = 4

_scraper = cloudscraper.create_scraper()
_consecutive_failures = 0


class BlockedError(RuntimeError):
    """Raised when _get() has failed CONSECUTIVE_FAILURE_LIMIT times in a
    row — almost certainly a sustained block, not transient trouble.
    Propagates all the way up to main(), which aborts the whole run."""


def _get(url: str, max_retries: int = 5) -> dict | None:
    global _consecutive_failures
    for attempt in range(1, max_retries + 1):
        try:
            resp = _scraper.get(url, timeout=20)
            if resp.status_code == 200:
                _consecutive_failures = 0
                return resp.json()
            if resp.status_code in (403, 429, 500, 502, 503):
                wait = 2 * attempt
                print(f"    [{resp.status_code}] {url} — retrying in {wait}s "
                      f"(attempt {attempt}/{max_retries})")
                time.sleep(wait)
                continue
            print(f"    [{resp.status_code}] {url} — giving up on this request.")
            _consecutive_failures += 1
            _check_circuit_breaker()
            return None
        except Exception as exc:
            wait = 2 * attempt
            print(f"    network error on {url} ({exc}) — retrying in {wait}s")
            time.sleep(wait)
    print(f"    Failed after {max_retries} attempts: {url}")
    _consecutive_failures += 1
    _check_circuit_breaker()
    return None


def _check_circuit_breaker() -> None:
    if _consecutive_failures >= CONSECUTIVE_FAILURE_LIMIT:
        raise BlockedError(
            f"{_consecutive_failures} consecutive request failures — "
            f"almost certainly blocked. Aborting the whole run rather than "
            f"grinding through remaining events with guaranteed-to-fail "
            f"retries. Data committed so far (per-row commits) is safe."
        )


def fetch_rounds_meta(tournament_id: int, season_id: int) -> list[dict]:
    data = _get(f"{BASE_URL}/unique-tournament/{tournament_id}/season/{season_id}/rounds")
    time.sleep(REQUEST_DELAY_SECONDS)
    if not data:
        return []
    return data.get("rounds", [])


def fetch_events_for_round_meta(tournament_id: int, season_id: int, round_meta: dict) -> list[dict]:
    # Named rounds (qualifying rounds, knockout stages — anything with a
    # "slug") aren't reachable via the plain /events/round/{n} endpoint on
    # their own; that only returns the unnamed league-phase round sharing
    # the same round number. Verified directly against the live API —
    # /events/round/{n}/slug/{slug} is what actually returns them.
    round_num = round_meta["round"]
    slug = round_meta.get("slug")
    if slug:
        url = (f"{BASE_URL}/unique-tournament/{tournament_id}/season/{season_id}"
               f"/events/round/{round_num}/slug/{slug}")
    else:
        url = (f"{BASE_URL}/unique-tournament/{tournament_id}/season/{season_id}"
               f"/events/round/{round_num}")
    data = _get(url)
    time.sleep(REQUEST_DELAY_SECONDS)
    if not data:
        return []
    return data.get("events", [])


def fetch_expected_goals(event_id: int) -> tuple[float, float] | None:
    data = _get(f"{BASE_URL}/event/{event_id}/statistics")
    time.sleep(REQUEST_DELAY_SECONDS)
    if not data:
        return None
    for period_entry in data.get("statistics", []):
        if period_entry.get("period") != "ALL":
            continue
        for group in period_entry.get("groups", []):
            for item in group.get("statisticsItems", []):
                if item.get("key") == "expectedGoals":
                    home_xg, away_xg = item.get("homeValue"), item.get("awayValue")
                    if home_xg is not None and away_xg is not None:
                        return float(home_xg), float(away_xg)
    return None


def fetch_all_events_for_season(tournament_id: int, season_id: int) -> list[dict]:
    """Every event in a season, regardless of whether it's a plain league
    round or a named/slugged round (qualifying, knockout stage, etc.).
    Deduped by (round, slug) fetch and again by event id — some round
    entries repeat, and a handful of qualifying-playoff events showed up
    under more than one round listing in spot checks."""
    rounds = fetch_rounds_meta(tournament_id, season_id)
    if not rounds:
        return []
    seen_round_keys = set()
    events_by_id: dict[int, dict] = {}
    for r in rounds:
        key = (r["round"], r.get("slug"))
        if key in seen_round_keys:
            continue
        seen_round_keys.add(key)
        for event in fetch_events_for_round_meta(tournament_id, season_id, r):
            events_by_id[event["id"]] = event
    return list(events_by_id.values())


def fetch_all_events_paginated(tournament_id: int, season_id: int) -> list[dict]:
    """Alternate to fetch_all_events_for_season, for leagues whose
    SofaScore /rounds listing doesn't cover the regular season at all —
    confirmed for MLS: its /rounds endpoint only returns two named
    playoff rounds (conference finals), nothing for the ~400+ match
    regular season, because MLS doesn't play a clean single-round-robin
    schedule the way European leagues do. GET
    .../events/last/{page} (0-indexed, 30 events/page, `hasNextPage`
    flag) walks the season's full match list independent of round
    structure — confirmed directly against the live API (534 events
    found this way for a season /rounds only exposed 2 of). Opt-in via
    leagues_config's "use_paginated_events" key rather than switched to
    universally, since the round-based path is already proven correct
    for every other league using it."""
    events_by_id: dict[int, dict] = {}
    page = 0
    while True:
        data = _get(
            f"{BASE_URL}/unique-tournament/{tournament_id}/season/{season_id}/events/last/{page}"
        )
        time.sleep(REQUEST_DELAY_SECONDS)
        if not data:
            break
        for event in data.get("events", []):
            events_by_id[event["id"]] = event
        if not data.get("hasNextPage"):
            break
        page += 1
    return list(events_by_id.values())


def event_passes_filter(event: dict, cfg: dict) -> bool:
    """Continental competitions (see leagues_config.SOFASCORE_LEAGUES) split
    one SofaScore tournament into two of our `leagues` rows — main comp vs
    qualifying — using each event's own event["tournament"]["name"], which
    SofaScore itself tags distinctly (e.g. "UEFA Europa League" vs "...,
    Qualification"). Leagues without either filter key always pass."""
    tournament_name = event.get("tournament", {}).get("name", "")
    require = cfg.get("require_name_contains")
    exclude = cfg.get("exclude_name_contains")
    if require and require not in tournament_name:
        return False
    if exclude and exclude in tournament_name:
        return False
    return True


def get_or_create_league(conn, name: str, country: str | None, kind: str) -> int:
    # "IS" (not "=") — country is NULL for international/continental
    # competitions, and "country = NULL" never matches in SQL regardless
    # of the bound value; "country IS ?" is the NULL-safe equivalent.
    cur = conn.execute("SELECT id FROM leagues WHERE name = ? AND country IS ?", (name, country))
    row = cur.fetchone()
    if row:
        return row[0]
    cur = conn.execute(
        "INSERT INTO leagues (name, country, kind) VALUES (?, ?, ?)", (name, country, kind)
    )
    return cur.lastrowid


def get_or_create_season(conn, league_id: int, year_start: int) -> int:
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


def get_or_create_team(conn, sofascore_id: int, name: str, country: str) -> int:
    alias = str(sofascore_id)
    cur = conn.execute(
        "SELECT team_id FROM team_aliases WHERE source = ? AND alias = ?", (SOURCE, alias)
    )
    row = cur.fetchone()
    if row:
        return row[0]

    cur = conn.execute("SELECT id FROM teams WHERE name = ? AND country = ?", (name, country))
    row = cur.fetchone()
    if row:
        team_id = row[0]
    else:
        logo_url = f"{BASE_URL}/team/{sofascore_id}/image"
        cur = conn.execute(
            "INSERT INTO teams (name, country, logo_url) VALUES (?, ?, ?)",
            (name, country, logo_url),
        )
        team_id = cur.lastrowid

    conn.execute(
        "INSERT OR IGNORE INTO team_aliases (team_id, source, alias) VALUES (?, ?, ?)",
        (team_id, SOURCE, alias),
    )
    return team_id


def parse_year_start(season_year: str) -> int:
    # SofaScore season.year is either cross-year "19/20" -> 2019, or a
    # plain 4-digit calendar year "2023" -> 2023 for leagues that run
    # within one calendar year (MLS, Eliteserien, Allsvenskan, Superettan,
    # K League 1, J1 League's pre-2026 seasons, Paraguay) — confirmed via
    # a real bug: treating "2023" as the 2-digit form computed
    # 2000 + int("2023") = 4023, corrupting seasons.year_start for every
    # calendar-year league. A 4-digit first segment is already the
    # answer; only the 2-digit form needs the +2000.
    first_segment = season_year.split("/")[0]
    return int(first_segment) if len(first_segment) == 4 else 2000 + int(first_segment)


def insert_event(conn, league_id: int, season_id: int, event: dict, fetch_xg: bool = False) -> bool:
    home = event["homeTeam"]
    away = event["awayTeam"]
    home_id = get_or_create_team(conn, home["id"], home["name"], home.get("country", {}).get("name") or "")
    away_id = get_or_create_team(conn, away["id"], away["name"], away.get("country", {}).get("name") or "")
    # get_or_create_team above may have INSERTed a new team, leaving an
    # open write transaction. Commit it here, before the network-bound xG
    # fetch below — otherwise that transaction (and the write lock it can
    # need) stays open for however long the network call/its retries take
    # (up to ~30s in the worst case), which is long enough to blow past
    # other concurrent workers' own busy_timeout and crash them with
    # "database is locked". Hit exactly this running 3 workers in
    # parallel; a plain commit here is cheap and fixes it.
    conn.commit()

    is_finished = event.get("status", {}).get("type") == "finished"
    home_goals = event.get("homeScore", {}).get("current") if is_finished else None
    away_goals = event.get("awayScore", {}).get("current") if is_finished else None
    status = "finished" if is_finished else "scheduled"
    match_date = datetime.fromtimestamp(event["startTimestamp"], tz=UTC).strftime("%Y-%m-%d")
    source_match_id = str(event["id"])

    # fetch_xg is opt-in per league (see leagues_config's "fetch_xg" key) —
    # Iran/Iraq/continental competitions were loaded before this existed
    # and deliberately stay goals-only (see module docstring); leagues with
    # no other pre-existing source (nothing for sofascore_xg_enrich.py to
    # match against later) need xG captured here instead.
    home_xg = away_xg = None
    if fetch_xg and is_finished:
        xg = fetch_expected_goals(event["id"])
        if xg:
            home_xg, away_xg = xg

    conn.execute(
        """
        INSERT INTO matches (
            league_id, season_id, match_date, home_team_id, away_team_id,
            home_goals, away_goals, home_xg, away_xg, status,
            is_neutral_venue, source, source_match_id
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?)
        ON CONFLICT(source, source_match_id) DO UPDATE SET
            home_goals=excluded.home_goals,
            away_goals=excluded.away_goals,
            status=excluded.status,
            home_xg=COALESCE(excluded.home_xg, home_xg),
            away_xg=COALESCE(excluded.away_xg, away_xg)
        """,
        (league_id, season_id, match_date, home_id, away_id,
         home_goals, away_goals, home_xg, away_xg, status, SOURCE, source_match_id),
    )
    return is_finished


def load_league(conn, key: str, cfg: dict) -> int:
    league_id = get_or_create_league(conn, cfg["name"], cfg["country"], cfg["kind"])
    conn.commit()  # release a possible new-league write before the network calls — see insert_event
    fetch_xg = cfg.get("fetch_xg", False)
    use_paginated = cfg.get("use_paginated_events", False)
    total = 0
    for season_id in cfg["season_ids"]:
        events = (
            fetch_all_events_paginated(cfg["tournament_id"], season_id) if use_paginated
            else fetch_all_events_for_season(cfg["tournament_id"], season_id)
        )
        if not events:
            print(f"  [{cfg['name']}] season {season_id}: no events found, skipping.")
            continue
        matched_events = [e for e in events if event_passes_filter(e, cfg)]

        season_year_start = None
        season_db_id = None
        season_finished = 0
        for event in matched_events:
            if season_year_start is None:
                season_year_start = parse_year_start(event["season"]["year"])
                season_db_id = get_or_create_season(conn, league_id, season_year_start)
            if insert_event(conn, league_id, season_db_id, event, fetch_xg=fetch_xg):
                season_finished += 1
            # Per-event commit (not once per season): fetch_xg leagues run
            # one extra network call per finished match, and this can run
            # as several parallel processes against the same file — a
            # season-long open write transaction is exactly what starves
            # concurrent writers into "database is locked" (see
            # sofascore_xg_enrich.py's enrich_league, which hit this for
            # real and was fixed the same way).
            conn.commit()
        print(f"  [{cfg['name']}] season {season_year_start or season_id}: "
              f"{season_finished} finished match(es) loaded/updated "
              f"({len(matched_events)}/{len(events)} events matched this league's filter).")
        total += season_finished
    return total


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Load Iran/Iraq (or other configured SofaScore) league data into football.db"
    )
    parser.add_argument("--db", default="data/football.db")
    parser.add_argument(
        "--leagues",
        nargs="+",
        default=list(SOFASCORE_LEAGUES.keys()),
        help=f"Subset of leagues to load. Options: {list(SOFASCORE_LEAGUES.keys())}",
    )
    args = parser.parse_args()

    unknown = [k for k in args.leagues if k not in SOFASCORE_LEAGUES]
    if unknown:
        parser.error(f"Unknown league(s): {unknown}")

    # timeout=30 + WAL: same concurrent-write fix as sofascore_xg_enrich.py
    # — this can run as several parallel processes against the same file.
    conn = sqlite3.connect(args.db, timeout=30)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")

    grand_total = 0
    try:
        for key in args.leagues:
            cfg = SOFASCORE_LEAGUES[key]
            print(f"\n=== {cfg['name']} ({cfg['country']}) ===")
            grand_total += load_league(conn, key, cfg)
    except BlockedError as exc:
        conn.close()
        print(f"\nABORTED — {exc}")
        print(f"{grand_total} finished match(es) loaded/updated before aborting.")
        raise SystemExit(2) from None

    conn.close()
    print(f"\nDone. {grand_total} total finished matches loaded/updated across "
          f"{len(args.leagues)} league(s).")


if __name__ == "__main__":
    main()
