"""
Backfills home_xg/away_xg onto EXISTING matches (already in football.db
from football-data.co.uk/.org) for leagues that have fixtures/results but
no xG — see leagues_config.SOFASCORE_XG_ENRICH and
modeling/rating_engine.py's BLEND_GOALS_WEIGHT (xG blended 75% with goals
when present; pure goals otherwise).

Unlike sofascore_scraper.py, this NEVER creates a new team or match row —
only UPDATEs xG onto a row it can confidently match by (league, team
pair, date within 1 day). This distinction matters: an earlier attempt to
treat an already-populated league (Champions League) as a fresh-insert
target produced duplicate teams, because team names differ slightly
between sources ("AS Monaco FC" vs "AS Monaco") and a naive name+country
lookup missed the existing row entirely. Team matching here is scoped to
teams that have actually played in the specific league being enriched
(a few dozen candidates, not the whole database) and normalized (accents,
case, common suffixes like FC/CF/SC stripped) before comparing — narrow
and lenient enough to bridge most naming differences safely. Anything it
still can't confidently resolve to exactly one team and exactly one
existing match is reported, not guessed — same principle as
db/fix_team_countries.py's "ambiguous — left untouched" handling.

Usage:
    python sofascore_xg_enrich.py --db data/football.db
    python sofascore_xg_enrich.py --db data/football.db --leagues Championship
"""

import argparse
import re
import sqlite3
import unicodedata
from datetime import UTC, datetime

from leagues_config import SOFASCORE_XG_ENRICH
from sofascore_scraper import (
    event_passes_filter,
    fetch_all_events_for_season,
    fetch_expected_goals,
)
from thesportsdb_enrich import NAME_ALIASES as _THESPORTSDB_ALIASES

# football-data.co.uk's short English-league names ("Birmingham",
# "West Brom") that aren't already covered by thesportsdb_enrich's own
# NAME_ALIASES (reused above) — found by running the enrichment once and
# reading the "unresolved" report, same discovery process as the existing
# alias dict itself.
_EXTRA_ALIASES = {
    "Birmingham": "Birmingham City",
    "Blackburn": "Blackburn Rovers",
    "Coventry": "Coventry City",
    "Derby": "Derby County",
    "Hull": "Hull City",
    "Leicester": "Leicester City",
    "Norwich": "Norwich City",
    "Oxford": "Oxford United",
    "QPR": "Queens Park Rangers",
    "Sheffield Weds": "Sheffield Wednesday",
    "Stoke": "Stoke City",
    "West Brom": "West Bromwich Albion",
}
NAME_ALIASES = {**_THESPORTSDB_ALIASES, **_EXTRA_ALIASES}

_SUFFIX_RE = re.compile(r"\b(fc|cf|sc|afc|ac|calcio|cd|sk|club)\b")
_NON_ALNUM_RE = re.compile(r"[^a-z0-9]")


def normalize_name(name: str) -> str:
    name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    name = _SUFFIX_RE.sub("", name.lower())
    return _NON_ALNUM_RE.sub("", name)


def _register(lookup: dict[str, int | None], norm: str, team_id: int) -> None:
    if norm in lookup and lookup[norm] != team_id:
        lookup[norm] = None  # collision between two different teams — ambiguous, never guess
    else:
        lookup[norm] = team_id


def build_team_lookup(conn, league_id: int) -> dict[str, int | None]:
    """normalized name -> team_id, scoped to teams that have actually
    played in this league. Registers both the DB's own name and, when
    NAME_ALIASES has an entry for it (football-data.co.uk's short forms —
    "Birmingham" for "Birmingham City" — vs. SofaScore's fuller ones), the
    aliased full name too, so either form resolves to the same team. A
    collision between two genuinely different teams maps to None
    (ambiguous — never guess)."""
    rows = conn.execute(
        """
        SELECT DISTINCT t.id, t.name FROM teams t
        JOIN matches m ON t.id IN (m.home_team_id, m.away_team_id)
        WHERE m.league_id = ?
        """,
        (league_id,),
    ).fetchall()
    lookup: dict[str, int | None] = {}
    for team_id, name in rows:
        _register(lookup, normalize_name(name), team_id)
        if name in NAME_ALIASES:
            _register(lookup, normalize_name(NAME_ALIASES[name]), team_id)
    return lookup


def resolve_team(lookup: dict[str, int | None], sofascore_name: str) -> int | None:
    norm = normalize_name(sofascore_name)
    team_id = lookup.get(norm)
    if team_id:
        return team_id
    # Fallback: substring match against the league's own (small, already
    # narrowed) candidate list — only trusted if exactly one candidate
    # matches, so it can't silently guess wrong between two similarly
    # named teams in the same league.
    candidates = {
        tid for key, tid in lookup.items()
        if tid and (key in norm or norm in key)
    }
    return candidates.pop() if len(candidates) == 1 else None


def find_existing_match(conn, league_id: int, home_id: int, away_id: int, match_date: str):
    return conn.execute(
        """
        SELECT id, home_xg FROM matches
        WHERE league_id = ? AND home_team_id = ? AND away_team_id = ?
          AND date(match_date) BETWEEN date(?, '-1 day') AND date(?, '+1 day')
        """,
        (league_id, home_id, away_id, match_date, match_date),
    ).fetchall()


def enrich_league(conn, key: str, cfg: dict) -> None:
    row = conn.execute(
        "SELECT id FROM leagues WHERE name = ?", (cfg["our_league_name"],)
    ).fetchone()
    if not row:
        print(f"  [{cfg['our_league_name']}] not found in this database, skipping.")
        return
    league_id = row[0]
    team_lookup = build_team_lookup(conn, league_id)

    updated = 0
    already_had_xg = 0
    unmatched_teams: set[str] = set()
    unmatched_matches = 0
    ambiguous_matches = 0

    for season_id in cfg["season_ids"]:
        events = fetch_all_events_for_season(cfg["tournament_id"], season_id)
        events = [
            e for e in events
            if event_passes_filter(e, cfg) and e.get("status", {}).get("type") == "finished"
        ]
        for event in events:
            home_name, away_name = event["homeTeam"]["name"], event["awayTeam"]["name"]
            home_id = resolve_team(team_lookup, home_name)
            away_id = resolve_team(team_lookup, away_name)
            if not home_id or not away_id:
                if not home_id:
                    unmatched_teams.add(home_name)
                if not away_id:
                    unmatched_teams.add(away_name)
                continue

            match_date = datetime.fromtimestamp(event["startTimestamp"], tz=UTC).strftime("%Y-%m-%d")
            candidates = find_existing_match(conn, league_id, home_id, away_id, match_date)
            if len(candidates) == 0:
                unmatched_matches += 1
                continue
            if len(candidates) > 1:
                ambiguous_matches += 1
                continue

            match_id, existing_xg = candidates[0]
            if existing_xg is not None:
                already_had_xg += 1
                continue

            xg = fetch_expected_goals(event["id"])
            if xg is None:
                continue
            conn.execute(
                "UPDATE matches SET home_xg = ?, away_xg = ? WHERE id = ?",
                (xg[0], xg[1], match_id),
            )
            updated += 1
            # Commit every match, not once per season — this can run as
            # several parallel processes against the same file (different
            # leagues each), and a season-long open write transaction was
            # exactly what made a concurrent run hit "database is locked"
            # in practice: one process holding a write lock for the
            # minutes it takes to process a whole season starves the
            # others. A per-row commit is cheap next to the network call
            # that dominates each iteration anyway.
            conn.commit()
        conn.commit()

    print(
        f"  [{cfg['our_league_name']}] {updated} match(es) enriched with xG, "
        f"{already_had_xg} already had it, {unmatched_matches} no matching row found, "
        f"{ambiguous_matches} ambiguous (skipped), "
        f"{len(unmatched_teams)} team name(s) unresolved."
    )
    if unmatched_teams:
        print(f"    Unresolved team names: {sorted(unmatched_teams)}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Backfill xG onto existing matches from SofaScore's match statistics"
    )
    parser.add_argument("--db", default="data/football.db")
    parser.add_argument(
        "--leagues",
        nargs="+",
        default=list(SOFASCORE_XG_ENRICH.keys()),
        help=f"Subset of leagues to enrich. Options: {list(SOFASCORE_XG_ENRICH.keys())}",
    )
    args = parser.parse_args()

    unknown = [k for k in args.leagues if k not in SOFASCORE_XG_ENRICH]
    if unknown:
        parser.error(f"Unknown league(s): {unknown}")

    # timeout=30 (vs the 5s default): how long to wait on a locked db
    # before raising, for the brief overlap between this process's own
    # commits and a concurrent one running against the same file (see
    # enrich_league's per-row commit). WAL mode makes that overlap far
    # shorter to begin with — a writer doesn't block readers, and only
    # blocks another *writer* for the instant of the commit itself.
    conn = sqlite3.connect(args.db, timeout=30)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")

    for key in args.leagues:
        cfg = SOFASCORE_XG_ENRICH[key]
        print(f"\n=== {cfg['our_league_name']} ===")
        enrich_league(conn, key, cfg)

    conn.close()
    print("\nDone.")


if __name__ == "__main__":
    main()
