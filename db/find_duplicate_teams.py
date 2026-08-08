"""
Finds teams that are probably the same real-world club but got stored as
separate rows because different sources spell the name differently
(e.g. Understat's "Manchester City" vs football-data.org's "Manchester
City FC").

This does NOT merge anything automatically — it only prints candidate
pairs for you to review, because an incorrect merge is much harder to
undo than a missed one. Confirmed pairs go into merge_teams.py.

Usage:
    python find_duplicate_teams.py --db football.db
    python find_duplicate_teams.py --db football.db --threshold 0.85   # stricter match
"""

import argparse
import os
import re
import sqlite3
import unicodedata

# Common English-exonym vs local-name mismatches that pure token/accent
# normalization can't resolve on its own (different spelling, not just
# different accents) — e.g. "Munich" vs "München" share zero letters in
# the differing syllable. Add to this as new mismatches turn up.
EXONYM_MAP = {
    "munchen": "munich",
    "munich": "munich",
    "athina": "athens",
    "athens": "athens",
    "moskva": "moscow",
    "moscow": "moscow",
    "warszawa": "warsaw",
    "warsaw": "warsaw",
    "praha": "prague",
    "prague": "prague",
    "roma": "rome",
    "milano": "milan",
    "torino": "turin",
    "genova": "genoa",
}

# Common club-name noise words that make identical clubs look different
# across sources. Stripped before comparing.
NOISE_WORDS = {
    "fc", "cf", "afc", "sc", "ac", "cd", "ud", "sd", "ca", "as",
    "club", "de", "futbol", "football", "calcio", "the",
}


def strip_accents(text: str) -> str:
    """München -> Munchen, Łódź -> Lodz, etc. — folds diacritics via Unicode decomposition."""
    normalized = unicodedata.normalize("NFKD", text)
    return "".join(c for c in normalized if not unicodedata.combining(c))


def normalize(name: str) -> str:
    name = strip_accents(name)
    name = name.lower()
    name = re.sub(r"[^a-z0-9\s]", " ", name)
    tokens = []
    for t in name.split():
        if t in NOISE_WORDS:
            continue
        tokens.append(EXONYM_MAP.get(t, t))
    return " ".join(tokens).strip()


def similarity(a: str, b: str) -> float:
    """
    Token-based similarity, not character-level — character similarity
    wrongly scores "Manchester City" vs "Manchester United" as ~0.81
    because of the shared "Manchester " prefix, even though they're
    different clubs.

    Combines two signals:
      - Jaccard overlap, for cases like "Real Madrid" vs "Real Madrid CF"
      - Containment, for cases like "PSV" vs "PSV Eindhoven" where one
        name is a strict subset of the other's tokens (short-form club
        names are common and Jaccard alone scores these too low)
    """
    tokens_a = set(a.split())
    tokens_b = set(b.split())
    if not tokens_a or not tokens_b:
        return 0.0

    intersection = tokens_a & tokens_b
    union = tokens_a | tokens_b
    jaccard = len(intersection) / len(union)

    contained = tokens_a <= tokens_b or tokens_b <= tokens_a
    containment_score = 1.0 if contained and intersection else 0.0

    return max(jaccard, containment_score)


def find_candidates(conn, threshold: float):
    teams = conn.execute("SELECT id, name FROM teams ORDER BY name").fetchall()
    normalized = [(tid, name, normalize(name)) for tid, name in teams]

    candidates = []
    seen_pairs = set()

    # Bucket by first normalized token to avoid an O(n^2) scan across all 527 teams
    buckets = {}
    for tid, name, norm in normalized:
        if not norm:
            continue
        key = norm.split()[0]
        buckets.setdefault(key, []).append((tid, name, norm))

    for bucket in buckets.values():
        for i in range(len(bucket)):
            for j in range(i + 1, len(bucket)):
                tid_a, name_a, norm_a = bucket[i]
                tid_b, name_b, norm_b = bucket[j]
                if tid_a == tid_b:
                    continue
                pair_key = tuple(sorted([tid_a, tid_b]))
                if pair_key in seen_pairs:
                    continue
                seen_pairs.add(pair_key)

                score = similarity(norm_a, norm_b)
                if score >= threshold or norm_a == norm_b:
                    candidates.append((score, tid_a, name_a, tid_b, name_b))

    candidates.sort(key=lambda x: -x[0])
    return candidates


def get_team_context(conn, team_id):
    """Show which leagues/sources a team appears in, to help judge if a match is real."""
    rows = conn.execute(
        """
        SELECT DISTINCT l.name, m.source
        FROM matches m
        JOIN leagues l ON m.league_id = l.id
        WHERE m.home_team_id = ? OR m.away_team_id = ?
        """,
        (team_id, team_id),
    ).fetchall()
    return rows


def main():
    parser = argparse.ArgumentParser(description="Find candidate duplicate teams for review")
    parser.add_argument("--db", default="data/football.db")
    parser.add_argument("--threshold", type=float, default=0.80, help="Similarity threshold, 0-1 (default 0.80)")
    parser.add_argument("--output", default="data/dedup_review/duplicate_team_candidates.csv")
    args = parser.parse_args()

    conn = sqlite3.connect(args.db)

    candidates = find_candidates(conn, args.threshold)
    print(f"Found {len(candidates)} candidate pairs (threshold={args.threshold})\n")

    import csv as csv_module
    os.makedirs(os.path.dirname(args.output) or ".", exist_ok=True)
    with open(args.output, "w", newline="") as f:
        writer = csv_module.writer(f)
        writer.writerow(["score", "team_id_a", "name_a", "leagues_a", "team_id_b", "name_b", "leagues_b", "MERGE(y/n)"])

        for score, tid_a, name_a, tid_b, name_b in candidates:
            ctx_a = get_team_context(conn, tid_a)
            ctx_b = get_team_context(conn, tid_b)
            leagues_a = "; ".join(sorted(set(f"{l}[{s}]" for l, s in ctx_a)))
            leagues_b = "; ".join(sorted(set(f"{l}[{s}]" for l, s in ctx_b)))

            print(f"{score:.2f}  '{name_a}' (id={tid_a})  <->  '{name_b}' (id={tid_b})")
            print(f"      A plays in: {leagues_a}")
            print(f"      B plays in: {leagues_b}\n")

            writer.writerow([f"{score:.3f}", tid_a, name_a, leagues_a, tid_b, name_b, leagues_b, ""])

    conn.close()
    print(f"\nWritten to {args.output}. Review it, fill in 'y' in the MERGE column for real duplicates,")
    print("then pass that file to merge_teams.py to actually merge them.")


if __name__ == "__main__":
    main()