"""
One-off/rerunnable enrichment: looks up each team in TheSportsDB's free
public API (test key "3", no signup) and stores its badge (logo) URL on the
`teams` row, for the app to render logos with.

Matching is disambiguated by country whenever we know it (derived from the
leagues the team has actually played matches in — ground truth, not a
guess): a name-only search like "Athletic Club" or "Ipswich" returns several
same-named clubs from different countries, and blindly taking the first
result silently attaches the wrong badge (and, in an earlier version of this
script, the wrong country — see db/fix_team_countries.py for the cleanup).
When a team's country isn't known in advance, we fall back to the first
result as a best effort. This script no longer writes `teams.country` at
all — that field is now sourced from match/league ground truth instead.

Skips teams that already have a logo_url unless --force is passed, so it's
safe to rerun after adding new teams to the DB.

Usage:
    python thesportsdb_enrich.py --db ../data/football.db
    python thesportsdb_enrich.py --db ../data/football.db --force --limit 20
"""

import argparse
import sqlite3
import time

import requests

API_URL = "https://www.thesportsdb.com/api/v1/json/3/searchteams.php"
REQUEST_DELAY_SECONDS = 1.0

# Our `teams.name` is often a short/abbreviated form (from football-data.co.uk
# et al) that TheSportsDB's search just doesn't match at all — e.g. "Newcastle"
# only returns the Australian "Newcastle Jets", never Newcastle United, no
# matter how country filtering is done. These map our short name to the
# canonical name that actually finds the right club. An alias is a manual,
# human-verified identification, so a lookup made via an alias skips the
# country cross-check below (it would otherwise wrongly reject e.g. Cardiff
# City / Swansea City, which TheSportsDB tags "Wales" while our own ground
# truth — the country of the league they play in — says "England").
NAME_ALIASES = {
    "Athletic Club": "Athletic Bilbao",
    "Newcastle": "Newcastle United",
    "Ipswich": "Ipswich Town",
    "Inter": "Inter Milan",
    "Wolves": "Wolverhampton Wanderers",
    "Nott'm Forest": "Nottingham Forest",
    "AFC Ajax": "Ajax",
    "Preston": "Preston North End",
    "Cardiff": "Cardiff City",
    "Swansea": "Swansea City",
    "Peterboro": "Peterborough United",
    "Santander": "Racing Santander",
    "Academica": "Academica Coimbra",
    "Nacional": "CD Nacional",
    "PSV": "PSV Eindhoven",
    "AS Monaco FC": "AS Monaco",
    "Roda": "Roda JC Kerkrade",
    "Graafschap": "De Graafschap",
    "Nijmegen": "NEC Nijmegen",
    "VVV Venlo": "VVV-Venlo",
    "For Sittard": "Fortuna Sittard",
    "Waalwijk": "RKC Waalwijk",
    "Almere City": "Almere City FC",
    "Den Haag": "ADO Den Haag",
    "Heracles": "Heracles Almelo",
    "Zwolle": "PEC Zwolle",
    "Utrecht": "FC Utrecht",
    "Cambuur": "SC Cambuur",
    "Twente": "FC Twente",
    "Heerenveen": "SC Heerenveen",
    "Groningen": "FC Groningen",
    "Reus Deportiu": "CF Reus Deportiu",
    "Andorra": "FC Andorra",
    "Oud-Heverlee Leuven": "OH Leuven",
}


def _known_countries(conn: sqlite3.Connection) -> dict[int, str]:
    """team_id -> country, only for teams whose real match history points
    at exactly one country (mirrors db/fix_team_countries.py's logic)."""
    rows = conn.execute(
        """
        SELECT t.id, l.country
        FROM teams t
        JOIN matches m ON t.id IN (m.home_team_id, m.away_team_id)
        JOIN leagues l ON l.id = m.league_id
        WHERE l.country IS NOT NULL AND l.country != ''
        GROUP BY t.id, l.country
        """
    ).fetchall()
    by_team: dict[int, set[str]] = {}
    for team_id, country in rows:
        by_team.setdefault(team_id, set()).add(country)
    return {
        team_id: next(iter(countries))
        for team_id, countries in by_team.items()
        if len(countries) == 1
    }


def lookup_team(name, expected_country=None, trust_match=False, max_retries=3):
    for attempt in range(1, max_retries + 1):
        try:
            resp = requests.get(API_URL, params={"t": name}, timeout=15)
            if resp.status_code == 429:
                print("    Rate limited by TheSportsDB — stopping early, rerun later.")
                raise SystemExit(1)
            if not resp.ok:
                return None
            data = resp.json()
            candidates = data.get("teams") or []
            if not candidates:
                return None
            if trust_match:
                return {"logo_url": candidates[0].get("strBadge")}
            if expected_country:
                target = expected_country.strip().lower()
                match = next(
                    (c for c in candidates
                     if (c.get("strCountry") or "").strip().lower() == target),
                    None,
                )
                if match is None:
                    return None  # No candidate matches this team's real country — don't guess.
                return {"logo_url": match.get("strBadge")}
            return {"logo_url": candidates[0].get("strBadge")}
        except (requests.exceptions.RequestException, ValueError) as exc:
            if attempt == max_retries:
                print(f"    Failed after {max_retries} attempts ({exc}) — skipping.")
                return None
            time.sleep(2 * attempt)
    return None


def main():
    parser = argparse.ArgumentParser(description="Backfill team logos from TheSportsDB")
    parser.add_argument("--db", default="data/football.db")
    parser.add_argument(
        "--force", action="store_true", help="Re-fetch teams that already have a logo_url"
    )
    parser.add_argument(
        "--limit", type=int, default=None, help="Only process the first N teams (for testing)"
    )
    args = parser.parse_args()

    conn = sqlite3.connect(args.db)
    known_countries = _known_countries(conn)

    if args.force:
        rows = conn.execute("SELECT id, name FROM teams ORDER BY name").fetchall()
    else:
        # Empty string (not just NULL) means "already tried, no confident
        # match" — also worth retrying since NAME_ALIASES may now cover it.
        rows = conn.execute(
            "SELECT id, name FROM teams WHERE logo_url IS NULL OR logo_url = '' ORDER BY name"
        ).fetchall()

    if args.limit:
        rows = rows[: args.limit]

    print(f"Enriching {len(rows)} team(s)...")
    matched = 0
    for i, (team_id, name) in enumerate(rows, 1):
        alias = NAME_ALIASES.get(name)
        info = lookup_team(
            alias or name,
            expected_country=known_countries.get(team_id),
            trust_match=alias is not None,
        )
        time.sleep(REQUEST_DELAY_SECONDS)

        if info and info["logo_url"]:
            conn.execute("UPDATE teams SET logo_url = ? WHERE id = ?", (info["logo_url"], team_id))
            matched += 1
            status = "OK"
        else:
            conn.execute("UPDATE teams SET logo_url = '' WHERE id = ?", (team_id,))
            status = "no confident match"

        if i % 25 == 0 or i == len(rows):
            conn.commit()
            print(f"  [{i}/{len(rows)}] {matched} matched so far ({name}: {status})")

    conn.commit()
    print(f"\nDone. {matched}/{len(rows)} teams matched a logo.")


if __name__ == "__main__":
    main()
