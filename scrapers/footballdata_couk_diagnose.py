"""
Quick diagnostic — run this FIRST to see exactly what football-data.co.uk's
CSV files actually look like right now, before we trust column names in the
loader. Doesn't touch the database.

Checks:
  1. A "main league" CSV (per-season file) — e.g. England Championship
  2. An "extra league" CSV (all-seasons-in-one file) — e.g. Austria Bundesliga

Usage:
    python footballdata_couk_diagnose.py
"""

import requests

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    )
}

# Main-league pattern (per-season file): /mmz4281/{season_code}/{div_code}.csv
# season_code "2526" = 2025/26. div_code "E1" = England Championship.
MAIN_LEAGUE_URL = "https://www.football-data.co.uk/mmz4281/2526/E1.csv"

# Extra-league pattern (all seasons in one file): /new/{CODE}.csv
# This URL pattern is less certain — flagging that explicitly below.
EXTRA_LEAGUE_URL = "https://www.football-data.co.uk/new/AUT.csv"


def check_csv(label, url):
    print(f"\n=== {label} ===")
    print(f"URL: {url}")
    try:
        resp = requests.get(url, headers=HEADERS, timeout=20)
        print(f"Status: {resp.status_code}")
        print(f"Content-Type: {resp.headers.get('Content-Type')}")
        if not resp.ok:
            print("Non-200 response — URL pattern may be wrong.")
            return
        text = resp.text
        lines = text.splitlines()
        print(f"Total lines: {len(lines)}")
        if lines:
            header = lines[0]
            cols = [c.strip() for c in header.split(",")]
            print(f"Columns ({len(cols)}): {cols}")
        if len(lines) > 1:
            print(f"Sample row: {lines[1]}")
        if len(lines) > 2:
            print(f"Sample row 2: {lines[2]}")
    except requests.RequestException as exc:
        print(f"Request failed: {exc}")


if __name__ == "__main__":
    check_csv("Main league (England Championship, 2025/26)", MAIN_LEAGUE_URL)
    check_csv("Extra league (Austria Bundesliga, all seasons)", EXTRA_LEAGUE_URL)
    print("\nDone. Share this output so we can confirm/fix column mapping in the loader.")
