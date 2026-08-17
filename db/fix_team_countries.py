"""
One-off/rerunnable correction: thesportsdb_enrich.py used to backfill a
blank `teams.country` from a plain name search against TheSportsDB's API
(see git history) — for generic team names ("Athletic Club", "Ipswich",
"Inter", ...) that search can match a same-named club in a different
country, silently mistagging the team's flag/country.

This script re-derives the correct country from ground truth we already
have: the country of the league(s) a team has actually played matches in
(teams -> matches -> leagues). Only touches teams whose real match history
points at exactly one country that disagrees with the stored value —
ambiguous/multi-country teams are left untouched and reported.

That "league's country = team's country" assumption is itself wrong for a
known handful of real cross-border clubs — a league's own country tag is
about where the COMPETITION is run, not where every member club is based.
Caught during the 14-league expansion: MLS is tagged country="USA" but
includes three genuinely Canadian franchises, and Switzerland's Super
League includes FC Vaduz, genuinely based in Liechtenstein (which has no
top-flight league of its own). Applying this script's normal "unambiguous"
logic to them would silently overwrite a correct country with a wrong one
— excluded by name below rather than trusted just because the query found
only one candidate country.

Usage:
    python fix_team_countries.py --db ../data/football.db          # dry run
    python fix_team_countries.py --db ../data/football.db --apply
"""

import argparse
import sqlite3

# Real clubs based in a different country than the league they play in —
# see module docstring. Keyed by name since that's what's visible in the
# dry-run output; a name collision with an unrelated same-named club
# elsewhere is exactly the kind of ambiguity this script already reports
# rather than guesses at, so a name-only match is as safe here as anywhere
# else in this script.
KNOWN_CROSS_BORDER_CLUBS = {"Vancouver Whitecaps", "CF Montréal", "Toronto FC", "FC Vaduz"}


def find_mismatches(conn: sqlite3.Connection) -> tuple[list[tuple], list[tuple]]:
    rows = conn.execute(
        """
        SELECT t.id, t.name, t.country, l.country, COUNT(*)
        FROM teams t
        JOIN matches m ON t.id IN (m.home_team_id, m.away_team_id)
        JOIN leagues l ON l.id = m.league_id
        WHERE l.country IS NOT NULL AND l.country != ''
        GROUP BY t.id, l.country
        """
    ).fetchall()

    by_team: dict[tuple[int, str, str | None], set[str]] = {}
    for team_id, name, stored, real_country, _n in rows:
        by_team.setdefault((team_id, name, stored), set()).add(real_country)

    clean, ambiguous = [], []
    for (team_id, name, stored), countries in by_team.items():
        if stored in countries:
            continue
        if name in KNOWN_CROSS_BORDER_CLUBS:
            note = "<cross-border club, see KNOWN_CROSS_BORDER_CLUBS>"
            ambiguous.append((team_id, name, stored, sorted(countries) + [note]))
        elif len(countries) == 1:
            clean.append((team_id, name, stored, next(iter(countries))))
        else:
            ambiguous.append((team_id, name, stored, sorted(countries)))
    return clean, ambiguous


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Fix mistagged teams.country using real match/league data"
    )
    parser.add_argument("--db", default="data/football.db")
    parser.add_argument("--apply", action="store_true", help="Write the fixes (default is dry run)")
    args = parser.parse_args()

    conn = sqlite3.connect(args.db)
    clean, ambiguous = find_mismatches(conn)

    print(f"{len(clean)} team(s) with an unambiguous correct country:")
    applied = 0
    for team_id, name, stored, correct in clean:
        collision = conn.execute(
            "SELECT id FROM teams WHERE name = ? AND country = ? AND id != ?",
            (name, correct, team_id),
        ).fetchone()
        if collision:
            print(f"  SKIP  #{team_id:<5} {name!r}: {stored!r} -> {correct!r} "
                  f"(collides with existing team #{collision[0]} — needs manual merge)")
            continue
        verb = "FIX" if args.apply else "WOULD FIX"
        print(f"  {verb:<9} #{team_id:<5} {name!r}: {stored!r} -> {correct!r}")
        if args.apply:
            conn.execute(
                "UPDATE teams SET country = ?, logo_url = NULL WHERE id = ?", (correct, team_id)
            )
            applied += 1

    if ambiguous:
        print(f"\n{len(ambiguous)} team(s) played in multiple countries "
              f"— left untouched, review by hand:")
        for team_id, name, stored, countries in ambiguous:
            print(f"  #{team_id:<5} {name!r}: stored={stored!r} real_countries={countries}")

    if args.apply:
        conn.commit()
        print(f"\nApplied {applied} fix(es). logo_url cleared for corrected teams — "
              f"rerun thesportsdb_enrich.py to refetch their badges.")
    else:
        print("\nDry run — pass --apply to write these changes.")


if __name__ == "__main__":
    main()
