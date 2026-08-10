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

Usage:
    python fix_team_countries.py --db ../data/football.db          # dry run
    python fix_team_countries.py --db ../data/football.db --apply
"""

import argparse
import sqlite3


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
        if len(countries) == 1:
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
