"""
Finds and merges duplicate team rows — most often created when
SofaScore's own name for a club doesn't exactly match the name already
in `teams` from whatever source first tracked that club (football-
data.org, Understat, an earlier SofaScore fetch under a different
tournament), but a full audit (2026-08-29) found this can predate
SofaScore entirely too (football-data.co.uk's "Stuttgart" vs the
original "VfB Stuttgart", each with their own real, independent match
history, never merged by anything). get_or_create_team (sofascore_
scraper.py) only ever does an exact name+country match — deliberately,
since guessing at a fuzzy match at *creation* time risks silently
merging two genuinely different clubs. This script runs the fuzzy match
*after the fact* instead, comparing every team against every other team
in the same country, where a wrong merge is easy to catch (it always
requires an exact match after normalizing both names) and easy to have
caught zero false positives across 55 real merges so far (Bayern
Munich/FC Bayern München, Liverpool/Liverpool FC, Hannover/Hannover 96,
Elversberg/SV 07 Elversberg, Stuttgart/VfB Stuttgart, etc.) before this
became a standing step.

Confirmed live 2026-08-27/29: a fresh SofaScore fixtures pass across all
45 tracked leagues created 40 duplicates in one run (Bayern, Barcelona,
Liverpool, PSG, Atlético Madrid among them) — every one of those clubs'
fixtures would have hit "No ratings found for team id N" the moment a
user clicked one, since the duplicate had zero match history to rate
from (and its logo_url pointed at whichever source created it, not the
one already shown elsewhere for that club — reported live as
mismatched/wrong-looking crests, same root cause). Silent until someone
clicks the wrong fixture, so this is now called automatically at the
end of both sofascore_weekly_fixtures.py's and sofascore_scraper.py's
main() — not something to remember to run by hand after every fetch.

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
    name = name.replace("ß", "ss")
    # German ASCII transliteration: some sources spell ü/ö/ä as the
    # literal accented character, others as the ue/oe/ae digraph
    # convention (both are standard German spelling) — found live
    # 2026-08-29: "Nuernberg" (34 matches, no logo) sitting unmerged next
    # to "Nurnberg" (381 matches) purely because of this, on top of
    # "Greuther Fuerth" vs whatever the accented form normalizes to.
    # Collapsed before the accent substitutions below so a name using
    # either spelling convention lands on the same normalized string.
    name = re.sub(r"ue", "u", name)
    name = re.sub(r"oe", "o", name)
    name = re.sub(r"ae", "a", name)
    name = re.sub(r"[àáâãäå]", "a", name)
    name = re.sub(r"[èéêë]", "e", name)
    name = re.sub(r"[ìíîï]", "i", name)
    name = re.sub(r"[òóôõöø]", "o", name)
    name = re.sub(r"[ùúûü]", "u", name)
    name = re.sub(r"[ç]", "c", name)
    name = re.sub(r"[ñ]", "n", name)
    name = re.sub(r"[ğ]", "g", name)
    name = re.sub(r"[ş]", "s", name)
    name = re.sub(r"[ı]", "i", name)
    name = re.sub(r"\b(fc|cf|sc|ac|fk|sk|sv|vfb|vfl|tsv|1\.|club|calcio|de|club de)\b", "", name)
    name = re.sub(r"[0-9]", "", name)
    name = re.sub(r"[^a-z]", "", name)
    return name


def find_duplicate_pairs(conn: sqlite3.Connection) -> list[tuple[int, int, str, str]]:
    """Returns (new_id, orig_id, new_name, orig_name) for every pair of
    teams whose normalized names match and whose countries don't
    positively conflict. Scans every team, not just SofaScore-sourced
    ones — a full audit (2026-08-29) found this project already had at
    least one pre-existing duplicate with no SofaScore involvement at
    all (football-data.co.uk's "Stuttgart" vs the original "VfB
    Stuttgart", each with its own real match history, never merged).

    country=None is treated as a wildcard, not a value to match
    exactly — the same audit found this is systematically how
    continental-competition teams land in `teams` (the UEFA fetches
    that create them don't carry a domestic country), and several of
    those turned out to be plain duplicates of a domestic-league row
    that DOES have a country ("Lommel SK" / Belgium, "Royale Union
    Saint-Gilloise" / Belgium, "Rodina" / "Rodina Moscow" / Russia).
    Requiring exact country equality would silently keep comparing
    those against nothing forever. Two teams that BOTH have a set,
    different country are never matched, regardless of name — that
    stays a hard rule, since a real cross-country name collision (two
    countries both having a club called "Union", say) is exactly the
    false-positive risk country-scoping exists to prevent; None is
    "unknown," not "no country," so it doesn't carry that same risk.

    Which side is `new_id` (merged away) vs `orig_id` (kept) is decided
    by match count, not id order — id order is just insertion order
    across however many different scraper runs over however much time,
    not a reliable signal for which record is more authoritative. The
    Stuttgart pair above is exactly why this matters: the *lower*-id
    team ("Stuttgart", 68 matches) turned out to be the thinner record;
    the *higher*-id one ("VfB Stuttgart", 363 matches, an Understat
    alias too) is the one worth keeping.

    Having a country set outranks match count, though — found live
    2026-08-29: "Rodina" (country=None, 31 matches — a continental-
    competition fetch that had already accumulated real history) vs
    "Rodina Moscow" (country='Russia', only 7 matches). Match count
    alone would have kept the *None*-country record and thrown away
    the one real fact (its actual country) the other side had. Ties on
    both fall back to lower id."""
    all_teams = conn.execute("SELECT id, name, country FROM teams").fetchall()
    match_counts = dict(
        conn.execute(
            """
            SELECT team_id, COUNT(*) FROM (
                SELECT home_team_id AS team_id FROM matches
                UNION ALL
                SELECT away_team_id AS team_id FROM matches
            ) GROUP BY team_id
            """
        ).fetchall()
    )

    by_norm: dict[str, list[tuple[int, str, str | None]]] = {}
    for tid, name, country in all_teams:
        norm = normalize_team_name(name)
        if norm:
            by_norm.setdefault(norm, []).append((tid, name, country))

    pairs = []
    for norm, teams in by_norm.items():
        for i, (tid1, name1, country1) in enumerate(teams):
            for tid2, name2, country2 in teams[i + 1:]:
                if country1 is not None and country2 is not None and country1 != country2:
                    continue
                # (has_country, match_count) — country presence wins first,
                # match count only breaks a tie between two teams that
                # either both have a country or both don't.
                rank1 = (country1 is not None, match_counts.get(tid1, 0))
                rank2 = (country2 is not None, match_counts.get(tid2, 0))
                if rank1 > rank2 or (rank1 == rank2 and tid1 < tid2):
                    orig_id, orig_name, new_id, new_name = tid1, name1, tid2, name2
                else:
                    orig_id, orig_name, new_id, new_name = tid2, name2, tid1, name1
                pairs.append((new_id, orig_id, new_name, orig_name))
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
