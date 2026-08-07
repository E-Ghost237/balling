"""
One-off/rerunnable enrichment: looks up each team in TheSportsDB's free
public API (test key "3", no signup) and stores its badge (logo) URL and
country name on the `teams` row, for the app to render flags/logos with.

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


def lookup_team(name, max_retries=3):
    for attempt in range(1, max_retries + 1):
        try:
            resp = requests.get(API_URL, params={"t": name}, timeout=15)
            if not resp.ok:
                return None
            data = resp.json()
            teams = data.get("teams") or []
            if not teams:
                return None
            best = teams[0]
            return {"logo_url": best.get("strBadge"), "country": best.get("strCountry")}
        except (requests.exceptions.RequestException, ValueError) as exc:
            if attempt == max_retries:
                print(f"    Failed after {max_retries} attempts ({exc}) — skipping.")
                return None
            time.sleep(2 * attempt)
    return None


def main():
    parser = argparse.ArgumentParser(description="Backfill team logos/countries from TheSportsDB")
    parser.add_argument("--db", default="data/football.db")
    parser.add_argument("--force", action="store_true", help="Re-fetch teams that already have a logo_url")
    parser.add_argument("--limit", type=int, default=None, help="Only process the first N teams (for testing)")
    args = parser.parse_args()

    conn = sqlite3.connect(args.db)

    if args.force:
        rows = conn.execute("SELECT id, name FROM teams ORDER BY name").fetchall()
    else:
        rows = conn.execute(
            "SELECT id, name FROM teams WHERE logo_url IS NULL ORDER BY name"
        ).fetchall()

    if args.limit:
        rows = rows[: args.limit]

    print(f"Enriching {len(rows)} team(s)...")
    matched = 0
    for i, (team_id, name) in enumerate(rows, 1):
        info = lookup_team(name)
        time.sleep(REQUEST_DELAY_SECONDS)

        if info and info["logo_url"]:
            conn.execute("UPDATE teams SET logo_url = ? WHERE id = ?", (info["logo_url"], team_id))
            if info["country"]:
                try:
                    conn.execute(
                        "UPDATE teams SET country = COALESCE(NULLIF(country, ''), ?) WHERE id = ?",
                        (info["country"], team_id),
                    )
                except sqlite3.IntegrityError:
                    # Another row already has this (name, country) pair — a pre-existing
                    # duplicate-team entry (see data/dedup_review/). Keep the logo, skip the country.
                    pass
            matched += 1
            status = "OK"
        else:
            conn.execute("UPDATE teams SET logo_url = '' WHERE id = ?", (team_id,))
            status = "no match"

        if i % 25 == 0 or i == len(rows):
            conn.commit()
            print(f"  [{i}/{len(rows)}] {matched} matched so far ({name}: {status})")

    conn.commit()
    print(f"\nDone. {matched}/{len(rows)} teams matched a logo.")


if __name__ == "__main__":
    main()
