"""
Finds and merges duplicate team rows created when SofaScore's own name
for a club doesn't exactly match the name already in `teams` from
whatever source first tracked that club (football-data.org, Understat,
an earlier SofaScore fetch under a different tournament). get_or_create_
team (sofascore_scraper.py) only ever does an exact name+country match —
deliberately, since guessing at a fuzzy match at *creation* time risks
silently merging two genuinely different clubs. This script runs the
fuzzy match *after the fact* instead, where a wrong merge is easy to
catch (it always requires an exact match after normalizing both names,
scoped to the same country) and easy to have caught zero false
positives across 54 real merges so far (Bayern Munich/FC Bayern
München, Liverpool/Liverpool FC, Hannover/Hannover 96, Elversberg/SV 07
Elversberg, etc.) before this became a standing step.

Confirmed live 2026-08-27/29: a fresh SofaScore fixtures pass across all
45 tracked leagues created 40 duplicates in one run (Bayern, Barcelona,
Liverpool, PSG, Atlético Madrid among them) — every one of those clubs'
fixtures would have hit "No ratings found for team id N" the moment a
user clicked one, since the duplicate had zero match history to rate
from. Silent until someone clicks the wrong fixture, so this is now
called automatically at the end of both sofascore_weekly_fixtures.py's
and sofascore_scraper.py's main() — not something to remember to run by
hand after every fetch.

The normalization is deliberately conservative: lowercase, strip
accents, strip a short list of common club-name tokens (fc/cf/sc/ac/sv/
vfb/vfl/tsv/"1."/club/calcio/de), strip all digits (catches founding-
year suffixes like "Hannover 96", "Darmstadt 98", "VfL Bochum 1848",
"SV 07 Elversberg"), then strip everything but letters. Only teams
sharing the same `country` are ever compared — cross-country name
collisions are a real risk with common short names (e.g. two different
countries could both have a "Union") that country-scoping avoids
without needing a stricter name filter.

Usage:
    python dedupe_teams.py --db data/football.db
"""

import argparse
import re
import sqlite3


def normalize_team_name(name: str) -> str:
    name = name.lower()
    name = re.sub(r"[àáâãäå]", "a", name)
    name = re.sub(r"[èéêë]", "e", name)
    name = re.sub(r"[ìíîï]", "i", name)
    name = re.sub(r"[òóôõöø]", "o", name)
    name = re.sub(r"[ùúûü]", "u", name)
    name = re.sub(r"[ç]", "c", name)
    name = re.sub(r"[ñ]", "n", name)
    name = re.sub(r"\b(fc|cf|sc|ac|sv|vfb|vfl|tsv|1\.|club|calcio|de|club de)\b", "", name)
    name = re.sub(r"[0-9]", "", name)
    name = re.sub(r"[^a-z]", "", name)
    return name


def find_duplicate_pairs(conn: sqlite3.Connection) -> list[tuple[int, int, str, str]]:
    """Returns (new_id, orig_id, new_name, orig_name) for every SofaScore-
    sourced team whose normalized name+country matches an existing,
    lower-id team. Only SofaScore-sourced teams are candidates for
    being the "new" (duplicate) side — every other source in this
    project already did its own team resolution when it was loaded."""
    sofa_teams = conn.execute(
        """
        SELECT t.id, t.name, t.country FROM teams t
        JOIN team_aliases ta ON ta.team_id = t.id
        WHERE ta.source = 'sofascore'
        """
    ).fetchall()
    all_teams = conn.execute("SELECT id, name, country FROM teams").fetchall()

    by_country: dict[str | None, list[tuple[int, str, str]]] = {}
    for tid, name, country in all_teams:
        by_country.setdefault(country, []).append((tid, name, normalize_team_name(name)))

    pairs = []
    for tid, name, country in sofa_teams:
        norm = normalize_team_name(name)
        if not norm:
            continue
        for oid, oname, onorm in by_country.get(country, []):
            if oid != tid and onorm == norm and oid < tid:
                pairs.append((tid, oid, name, oname))
    return pairs


def merge_duplicate_teams(conn: sqlite3.Connection) -> list[tuple[int, int, str, str]]:
    """Merges every duplicate pair found, committing one at a time so a
    failure on one pair (shouldn't happen — every referencing table is
    handled below — but if the schema ever grows a new one, this is
    where it'd surface) doesn't roll back the others. Returns the list
    of pairs actually merged."""
    merged = []
    for new_id, orig_id, new_name, orig_name in find_duplicate_pairs(conn):
        try:
            conn.execute("UPDATE matches SET home_team_id=? WHERE home_team_id=?", (orig_id, new_id))
            conn.execute("UPDATE matches SET away_team_id=? WHERE away_team_id=?", (orig_id, new_id))
            conn.execute("UPDATE shots SET team_id=? WHERE team_id=?", (orig_id, new_id))
            # Ratings aren't reassigned, just dropped for the duplicate —
            # team_ratings has UNIQUE(team_id, as_of_date), so a blind
            # reassign would collide with orig_id's own real rating for
            # the same date, and orig_id's rating (built from its real
            # history) is the one that should win regardless.
            conn.execute("DELETE FROM team_ratings WHERE team_id=?", (new_id,))
            conn.execute("UPDATE predictions SET home_team_id=? WHERE home_team_id=?", (orig_id, new_id))
            conn.execute("UPDATE predictions SET away_team_id=? WHERE away_team_id=?", (orig_id, new_id))
            conn.execute("UPDATE team_aliases SET team_id=? WHERE team_id=?", (orig_id, new_id))
            conn.execute("DELETE FROM teams WHERE id=?", (new_id,))
            conn.commit()
            merged.append((new_id, orig_id, new_name, orig_name))
        except Exception as exc:
            conn.rollback()
            print(f"  dedupe_teams: FAILED merging {new_id} ({new_name!r}) "
                  f"-> {orig_id} ({orig_name!r}): {exc}")
    return merged


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Find and merge duplicate teams created by SofaScore name mismatches"
    )
    parser.add_argument("--db", default="data/football.db")
    args = parser.parse_args()

    conn = sqlite3.connect(args.db, timeout=30)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")

    merged = merge_duplicate_teams(conn)
    for new_id, orig_id, new_name, orig_name in merged:
        print(f"  merged {new_id} ({new_name!r}) -> {orig_id} ({orig_name!r})")

    integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
    conn.close()
    print(f"\nDone. {len(merged)} duplicate(s) merged. integrity_check: {integrity}")


if __name__ == "__main__":
    main()
