"""
Batch-scrape all 6 Understat leagues across their full available history
(2014/15 season onward) into football.db.

This just loops understat_scraper.scrape_league_season() over every
league/season combination. It's safe to re-run — insert_match() upserts
on (source, source_match_id), so already-scraped matches get refreshed
rather than duplicated, and interrupted runs can just be restarted.

Usage:
    python batch_scrape.py --db football.db
    python batch_scrape.py --db football.db --from-season 2020   # partial backfill
    python batch_scrape.py --db football.db --with-shots         # much slower, one extra request per match
"""

import argparse
import sqlite3
import sys
import time
from datetime import datetime

from understat_scraper import LEAGUE_SLUGS, scrape_league_season

# Understat's earliest available season across all 6 leagues.
EARLIEST_SEASON = 2014


def current_season_start_year() -> int:
    """European domestic seasons run August-May, so a season's "start year"
    is the current year from July onward, and last year before that (same
    convention footballdata_couk_loader.py uses for its own season parsing)."""
    now = datetime.now()
    return now.year if now.month >= 7 else now.year - 1


# Current season start year, computed automatically so this doesn't need a
# manual bump every August.
LATEST_SEASON = current_season_start_year()


def main():
    parser = argparse.ArgumentParser(description="Batch scrape all Understat leagues/seasons")
    parser.add_argument("--db", default="data/football.db")
    parser.add_argument("--from-season", type=int, default=EARLIEST_SEASON)
    parser.add_argument("--to-season", type=int, default=LATEST_SEASON)
    parser.add_argument("--with-shots", action="store_true")
    parser.add_argument(
        "--leagues",
        nargs="+",
        default=list(LEAGUE_SLUGS.keys()),
        help=f"Subset of leagues to scrape. Options: {list(LEAGUE_SLUGS.keys())}",
    )
    args = parser.parse_args()

    seasons = list(range(args.from_season, args.to_season + 1))
    total_jobs = len(args.leagues) * len(seasons)
    print(f"Scraping {len(args.leagues)} leagues x {len(seasons)} seasons = {total_jobs} jobs\n")

    conn = sqlite3.connect(args.db)
    conn.execute("PRAGMA foreign_keys = ON")

    failures = []
    job_num = 0
    start_time = time.time()

    for league in args.leagues:
        for season in seasons:
            job_num += 1
            print(f"[{job_num}/{total_jobs}] {league} {season}/{season+1}")
            try:
                scrape_league_season(conn, league, season, fetch_shots=args.with_shots)
            except Exception as exc:
                print(f"  FAILED: {exc}")
                failures.append((league, season, str(exc)))

    conn.close()

    elapsed_min = (time.time() - start_time) / 60
    print(f"\nDone in {elapsed_min:.1f} minutes.")
    if failures:
        print(f"\n{len(failures)} job(s) failed:")
        for league, season, err in failures:
            print(f"  {league} {season}: {err}")
        print("\nRe-run with --leagues <failed_league> --from-season <X> --to-season <X> to retry just those.")
    else:
        print("All jobs succeeded.")


if __name__ == "__main__":
    main()
