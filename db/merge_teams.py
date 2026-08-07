"""
Merges confirmed duplicate teams, based on your reviewed
duplicate_team_candidates.csv (from find_duplicate_teams.py) with 'y'
filled into the MERGE column for real duplicates.

For each confirmed pair, keeps team_id_a and reassigns everything pointing
at team_id_b (matches as home/away, shots, team_ratings, team_aliases) to
team_id_a, then deletes team_id_b. This is destructive — it's wrapped in
a transaction so a failure rolls back cleanly, but back up football.db
first if you want a safety net beyond that.

Usage:
    python merge_teams.py --db football.db --csv duplicate_team_candidates.csv
    python merge_teams.py --db football.db --csv duplicate_team_candidates.csv --dry-run
"""

import argparse
import csv
import sqlite3


def merge_pair(conn, keep_id: int, merge_id: int, dry_run: bool = False):
    if keep_id == merge_id:
        return

    # These tables have no uniqueness constraint tying rows to a single
    # team, so blind reassignment is always safe.
    reassignable_tables = [
        ("matches", "home_team_id"),
        ("matches", "away_team_id"),
        ("shots", "team_id"),
        ("predictions", "home_team_id"),
        ("predictions", "away_team_id"),
    ]

    if dry_run:
        for table, column in reassignable_tables:
            count = conn.execute(
                f"SELECT COUNT(*) FROM {table} WHERE {column} = ?", (merge_id,)
            ).fetchone()[0]
            if count:
                print(f"    would reassign {count} row(s) in {table}.{column}")
        alias_count = conn.execute(
            "SELECT COUNT(*) FROM team_aliases WHERE team_id = ?", (merge_id,)
        ).fetchone()[0]
        print(f"    would reassign {alias_count} row(s) in team_aliases.team_id")
        rating_count = conn.execute(
            "SELECT COUNT(*) FROM team_ratings WHERE team_id = ?", (merge_id,)
        ).fetchone()[0]
        if rating_count:
            print(f"    would delete {rating_count} stale team_ratings row(s) for the merged-away id")
            print(f"    (re-run rating_engine.py after merging to get a correct combined rating)")
        print(f"    would delete team id {merge_id}")
        return

    for table, column in reassignable_tables:
        conn.execute(f"UPDATE {table} SET {column} = ? WHERE {column} = ?", (keep_id, merge_id))

    # team_ratings has UNIQUE(team_id, as_of_date) — reassigning merge_id's
    # rows to keep_id can collide if keep_id already has a snapshot for the
    # same date (very likely, since ratings are usually computed for all
    # teams at once). Deleting rather than reassigning is also more
    # correct: a merged team's true combined rating isn't meaningfully
    # derivable from two old separate snapshots anyway — it needs a fresh
    # rating_engine.py run over the now-unified match history.
    conn.execute("DELETE FROM team_ratings WHERE team_id = ?", (merge_id,))

    # team_aliases has a UNIQUE(source, alias) constraint — if both team rows
    # somehow have an alias from the same source, keep the existing one on
    # `keep_id` rather than erroring out.
    conn.execute(
        """
        DELETE FROM team_aliases
        WHERE team_id = ? AND (source, alias) IN (
            SELECT source, alias FROM team_aliases WHERE team_id = ?
        )
        """,
        (merge_id, keep_id),
    )
    conn.execute("UPDATE team_aliases SET team_id = ? WHERE team_id = ?", (keep_id, merge_id))

    conn.execute("DELETE FROM teams WHERE id = ?", (merge_id,))


def main():
    parser = argparse.ArgumentParser(description="Merge confirmed duplicate teams")
    parser.add_argument("--db", default="data/football.db")
    parser.add_argument("--csv", default="data/dedup_review/duplicate_team_candidates.csv")
    parser.add_argument("--dry-run", action="store_true", help="Show what would happen without changing the DB")
    args = parser.parse_args()

    conn = sqlite3.connect(args.db)
    conn.execute("PRAGMA foreign_keys = OFF")  # we're intentionally repointing FKs before cleanup

    # Tracks id -> id it was merged into, so a later CSV row referencing an
    # already-merged id gets redirected instead of silently no-op'ing (SQL
    # UPDATE/DELETE on a nonexistent id affects 0 rows without erroring,
    # which can hide a real chain-of-three merge like Newcastle / Newcastle
    # United / Newcastle United FC all pointing at each other).
    redirect = {}

    def resolve(team_id):
        while team_id in redirect:
            team_id = redirect[team_id]
        return team_id

    merged_count = 0
    skipped_noop = 0
    with open(args.csv, newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            decision = row.get("MERGE(y/n)", "").strip().lower()
            if decision != "y":
                continue

            tid_a = resolve(int(row["team_id_a"]))
            tid_b = resolve(int(row["team_id_b"]))

            if tid_a == tid_b:
                print(f"Skipping '{row['name_b']}' <-> '{row['name_a']}' — already unified via an earlier merge in this run")
                skipped_noop += 1
                continue

            print(f"Merging '{row['name_b']}' (id={tid_b}) into '{row['name_a']}' (id={tid_a})")
            merge_pair(conn, keep_id=tid_a, merge_id=tid_b, dry_run=args.dry_run)
            redirect[tid_b] = tid_a
            merged_count += 1

    if not args.dry_run:
        conn.commit()
    conn.close()

    verb = "would merge" if args.dry_run else "merged"
    print(f"\nDone. {verb} {merged_count} pair(s).")
    if skipped_noop:
        print(f"({skipped_noop} row(s) skipped as redundant — already unified earlier in this same run)")
    if args.dry_run:
        print("Re-run without --dry-run to actually apply these changes.")


if __name__ == "__main__":
    main()