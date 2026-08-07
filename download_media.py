"""
Downloads every team's logo (teams.logo_url) and every distinct country's
flag (via flags.flag_code_for_country) to static/, so app.py can render
badges as embedded data URIs with no network calls at runtime.

Rerunnable/incremental: skips files that already exist unless --force.

Usage:
    python download_media.py --db data/football.db
"""

import argparse
import os
import sqlite3
import time

import requests

from flags import flag_code_for_country

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
LOGOS_DIR = os.path.join(BASE_DIR, "static", "logos")
FLAGS_DIR = os.path.join(BASE_DIR, "static", "flags")
REQUEST_DELAY_SECONDS = 0.3


def download(url, dest_path, max_retries=3):
    for attempt in range(1, max_retries + 1):
        try:
            resp = requests.get(url, timeout=15)
            if resp.ok and resp.content:
                with open(dest_path, "wb") as f:
                    f.write(resp.content)
                return True
            return False
        except requests.exceptions.RequestException:
            if attempt == max_retries:
                return False
            time.sleep(2 * attempt)
    return False


def main():
    parser = argparse.ArgumentParser(description="Cache team logos and country flags locally")
    parser.add_argument("--db", default="data/football.db")
    parser.add_argument("--force", action="store_true", help="Re-download files that already exist")
    args = parser.parse_args()

    os.makedirs(LOGOS_DIR, exist_ok=True)
    os.makedirs(FLAGS_DIR, exist_ok=True)

    conn = sqlite3.connect(args.db)

    teams = conn.execute(
        "SELECT id, country, logo_url FROM teams WHERE logo_url IS NOT NULL AND logo_url != ''"
    ).fetchall()

    print(f"Caching logos for {len(teams)} team(s)...")
    logo_ok = logo_fail = logo_skipped = 0
    flag_codes_needed = set()

    for i, (team_id, country, logo_url) in enumerate(teams, 1):
        dest = os.path.join(LOGOS_DIR, f"{team_id}.png")
        if os.path.exists(dest) and not args.force:
            logo_skipped += 1
        elif download(logo_url, dest):
            logo_ok += 1
            time.sleep(REQUEST_DELAY_SECONDS)
        else:
            logo_fail += 1
            time.sleep(REQUEST_DELAY_SECONDS)

        code = flag_code_for_country(country)
        if code:
            flag_codes_needed.add(code)

        if i % 100 == 0 or i == len(teams):
            print(f"  [{i}/{len(teams)}] logos: {logo_ok} downloaded, {logo_skipped} already cached, {logo_fail} failed")

    print(f"\nCaching {len(flag_codes_needed)} unique flag(s)...")
    flag_ok = flag_fail = flag_skipped = 0
    for code in sorted(flag_codes_needed):
        dest = os.path.join(FLAGS_DIR, f"{code}.png")
        if os.path.exists(dest) and not args.force:
            flag_skipped += 1
            continue
        if download(f"https://flagcdn.com/w40/{code}.png", dest):
            flag_ok += 1
        else:
            flag_fail += 1
        time.sleep(REQUEST_DELAY_SECONDS)

    print(f"Flags: {flag_ok} downloaded, {flag_skipped} already cached, {flag_fail} failed")
    print(
        f"\nDone. Logos: {logo_ok + logo_skipped}/{len(teams)} available locally. "
        f"Flags: {flag_ok + flag_skipped}/{len(flag_codes_needed)} available locally."
    )


if __name__ == "__main__":
    main()
