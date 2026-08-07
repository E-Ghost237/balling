"""
Quick diagnostic — run this FIRST to see what Understat is actually
returning right now, before running the full scraper. It won't touch
the database; it just prints what it finds so we can see the real
response shape and adjust the scraper's field names if needed.

Usage:
    python understat_diagnose.py
"""

import json
import requests

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    )
}
API_HEADERS = {
    **HEADERS,
    "Accept": "application/json, text/javascript, */*",
    "X-Requested-With": "XMLHttpRequest",
}

LEAGUE = "EPL"
SEASON = 2025


def check_new_endpoint():
    url = f"https://understat.com/getLeagueData/{LEAGUE}/{SEASON}"
    print(f"\n[1] Trying new JSON endpoint: {url}")
    try:
        resp = requests.get(url, headers=API_HEADERS, timeout=20)
        print(f"    Status: {resp.status_code}")
        print(f"    Content-Type: {resp.headers.get('Content-Type')}")
        try:
            data = resp.json()
            print(f"    Top-level keys: {list(data.keys())}")
            for key in data:
                val = data[key]
                kind = type(val).__name__
                size = len(val) if hasattr(val, "__len__") else "n/a"
                print(f"      - {key}: {kind}, size={size}")
                if size != "n/a" and size:
                    sample = list(val.values())[0] if isinstance(val, dict) else val[0]
                    print(f"        sample item keys: {list(sample.keys()) if isinstance(sample, dict) else sample}")
            return True
        except ValueError:
            print("    Response is not valid JSON. First 300 chars:")
            print("    " + resp.text[:300].replace("\n", " "))
            return False
    except requests.RequestException as exc:
        print(f"    Request failed: {exc}")
        return False


def check_legacy_page():
    url = f"https://understat.com/league/{LEAGUE}/{SEASON}"
    print(f"\n[2] Trying legacy league page: {url}")
    try:
        resp = requests.get(url, headers=HEADERS, timeout=20)
        print(f"    Status: {resp.status_code}")
        html = resp.text
        for var_name in ("teamsData", "datesData", "statData", "playersData"):
            present = f"var {var_name}" in html or f"{var_name} =" in html
            print(f"    Contains '{var_name}': {present}")
        # Dump any <script> snippet that looks data-related, to eyeball the real format
        import re
        candidates = re.findall(r"var\s+(\w+)\s*=", html)
        print(f"    All 'var X =' names found in page: {sorted(set(candidates))[:20]}")
    except requests.RequestException as exc:
        print(f"    Request failed: {exc}")


if __name__ == "__main__":
    print(f"Diagnosing Understat access for league={LEAGUE}, season={SEASON}")
    new_ok = check_new_endpoint()
    check_legacy_page()
    print("\nDone. Share this output and we'll adjust understat_scraper.py to match whatever Understat is actually serving.")
