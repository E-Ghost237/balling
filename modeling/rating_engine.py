"""
Step 3 of the pipeline: Elo ratings + attack/defense strength scores.

Elo design:
  - Full carry-over across seasons — a team's rating is one continuous
    thread, never reset. Promotion/relegation and Champions League
    crossover matches are what let ratings stay comparable across
    different leagues (the same mechanism clubelo.com and similar
    systems rely on). This means leagues with almost no crossover
    (e.g. a second division whose teams rarely reach continental
    competition) will have weaker cross-league calibration — ratings
    *within* that league are still meaningful relative to each other,
    just less precisely comparable to a team from a totally different
    league they've never (even indirectly) played.
  - Standard World-Football-Elo-style update: expected score from the
    logistic function, actual result (1/0.5/0), a goal-difference
    multiplier so blowouts move ratings more than 1-0 wins, and a
    home-advantage bonus added to the home team's effective rating.
  - Competition difficulty is folded in as a K-factor multiplier per
    league (LEAGUE_WEIGHTS below) — results in bigger, stronger
    competitions move ratings more.

Attack/defense design:
  - Per match, blends actual goals with xG where available (25% goals /
    75% xG, per the original spec) — pure goals when xG is NULL
    (goals-only leagues). xG preference order: our own shot-based model
    (matches.home_xg_model/away_xg_model, from xg_model.py) first, then
    the source's own xG figure, then goals-only.
  - Time-decayed (moderate recency bias — half-life of ~180 days, so a
    match from 6 months ago carries half the weight of one yesterday).
  - Normalized against a global weighted-average baseline, using the
    SAME league-weight table as Elo, so a team's attack/defense numbers
    are comparable across competitions of different difficulty.

Usage:
    python rating_engine.py --db football.db
"""

import argparse
import json
import math
import sqlite3
from collections import defaultdict
from datetime import datetime

BASE_ELO = 1500
HOME_ADVANTAGE = 100  # Elo points added to the home team's effective rating
BASE_K = 32

# Weight on raw goals in the goals/xG blend; xG gets (1 - this).
# Only applies when xG is available — pure goals otherwise.
BLEND_GOALS_WEIGHT = 0.25

# Moderate recency weighting: a match this many days old carries half
# the weight of a match today.
DECAY_HALF_LIFE_DAYS = 180

# Competition difficulty, reused as both the Elo K-factor multiplier and
# the attack/defense normalization weight. Tunable — these are reasonable
# starting assumptions, not derived from data.
LEAGUE_WEIGHTS = {
    "UEFA Champions League": 1.5,
    "Premier League": 1.2,
    "La Liga": 1.2,
    "Bundesliga": 1.2,
    "Serie A": 1.2,
    "Ligue 1": 1.2,
    "Eredivisie": 1.0,
    "Primeira Liga": 1.0,
    "Belgian Pro League": 1.0,
    "Austrian Bundesliga": 1.0,
    "Russian Premier League": 1.0,
    "Championship": 0.8,
    "La Liga 2": 0.8,
    "Bundesliga 2": 0.8,
    "Serie B": 0.8,
    "Ligue 2": 0.8,
    # International competitions (see leagues_config.INTERNATIONAL_COMPETITIONS).
    "FIFA World Cup": 1.6,
    "FIFA World Cup Qualification": 1.0,
    "UEFA European Championship": 1.4,
    "UEFA European Championship Qualification": 0.9,
    "Africa Cup of Nations": 1.1,
    "Africa Cup of Nations Qualification": 0.7,
    "Copa América": 1.2,
    "Copa América Qualification": 0.8,
    "CONCACAF Gold Cup": 0.9,
    "CONCACAF Gold Cup Qualification": 0.6,
    "AFC Asian Cup": 0.9,
    "AFC Asian Cup Qualification": 0.6,
}
DEFAULT_LEAGUE_WEIGHT = 1.0


def parse_date(date_str: str) -> datetime:
    """
    All three sources' date strings start with YYYY-MM-DD regardless of
    what follows (space+time, ISO 'T'+'Z', or nothing) — so date-only
    precision is all we need and all three formats parse the same way.
    """
    return datetime.strptime(date_str[:10], "%Y-%m-%d")


def goal_diff_multiplier(goal_diff: int) -> float:
    gd = abs(goal_diff)
    if gd <= 1:
        return 1.0
    elif gd == 2:
        return 1.5
    else:
        return (11 + gd) / 8


def expected_score(home_elo: float, away_elo: float) -> float:
    dr = (home_elo + HOME_ADVANTAGE) - away_elo
    return 1 / (10 ** (-dr / 400) + 1)


def league_weight(league_name: str) -> float:
    return LEAGUE_WEIGHTS.get(league_name, DEFAULT_LEAGUE_WEIGHT)


def fetch_finished_matches(conn):
    return conn.execute(
        """
        SELECT m.match_date, m.home_team_id, m.away_team_id,
               m.home_goals, m.away_goals,
               COALESCE(m.home_xg_model, m.home_xg) AS home_xg,
               COALESCE(m.away_xg_model, m.away_xg) AS away_xg,
               l.name
        FROM matches m
        JOIN leagues l ON m.league_id = l.id
        WHERE m.status = 'finished' AND m.home_goals IS NOT NULL AND m.away_goals IS NOT NULL
        ORDER BY m.match_date ASC
        """
    ).fetchall()


def compute_elo(matches):
    """Processes every finished match once, chronologically, across all leagues combined."""
    elo = defaultdict(lambda: BASE_ELO)

    for date_str, home_id, away_id, hg, ag, hxg, axg, league_name in matches:
        we = expected_score(elo[home_id], elo[away_id])
        if hg > ag:
            w_home = 1.0
        elif hg < ag:
            w_home = 0.0
        else:
            w_home = 0.5

        g = goal_diff_multiplier(hg - ag)
        k = BASE_K * league_weight(league_name)
        delta = k * g * (w_home - we)

        elo[home_id] += delta
        elo[away_id] -= delta

    return elo


def compute_attack_defense(matches, as_of_date: datetime):
    decay_lambda = math.log(2) / DECAY_HALF_LIFE_DAYS

    team_for = defaultdict(float)
    team_against = defaultdict(float)
    team_weight = defaultdict(float)
    global_for_total = 0.0
    global_weight_total = 0.0

    for date_str, home_id, away_id, hg, ag, hxg, axg, league_name in matches:
        d = parse_date(date_str)
        days_ago = (as_of_date - d).days
        if days_ago < 0:
            continue  # guard against any scheduled/future rows slipping in

        w = math.exp(-decay_lambda * days_ago)
        lw = league_weight(league_name)
        effective_w = w * lw

        if hxg is not None and axg is not None:
            home_perf = BLEND_GOALS_WEIGHT * hg + (1 - BLEND_GOALS_WEIGHT) * hxg
            away_perf = BLEND_GOALS_WEIGHT * ag + (1 - BLEND_GOALS_WEIGHT) * axg
        else:
            home_perf = float(hg)
            away_perf = float(ag)

        team_for[home_id] += effective_w * home_perf
        team_against[home_id] += effective_w * away_perf
        team_weight[home_id] += effective_w

        team_for[away_id] += effective_w * away_perf
        team_against[away_id] += effective_w * home_perf
        team_weight[away_id] += effective_w

        global_for_total += effective_w * (home_perf + away_perf)
        global_weight_total += effective_w * 2

    global_avg_for = global_for_total / global_weight_total if global_weight_total else 1.0

    attack_strength = {}
    defense_weakness = {}
    xg_for_avg = {}
    xg_against_avg = {}

    for team_id, weight_sum in team_weight.items():
        if weight_sum == 0:
            continue
        avg_for = team_for[team_id] / weight_sum
        avg_against = team_against[team_id] / weight_sum
        attack_strength[team_id] = avg_for / global_avg_for
        defense_weakness[team_id] = avg_against / global_avg_for
        xg_for_avg[team_id] = avg_for
        xg_against_avg[team_id] = avg_against

    return attack_strength, defense_weakness, xg_for_avg, xg_against_avg, global_avg_for


def save_ratings(conn, elo, attack_strength, defense_weakness, xg_for_avg, xg_against_avg, as_of_date_str):
    all_team_ids = set(elo) | set(attack_strength)
    for team_id in all_team_ids:
        conn.execute(
            """
            INSERT INTO team_ratings (
                team_id, as_of_date, elo, attack_strength, defense_weakness,
                xg_for_avg, xg_against_avg
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(team_id, as_of_date) DO UPDATE SET
                elo=excluded.elo,
                attack_strength=excluded.attack_strength,
                defense_weakness=excluded.defense_weakness,
                xg_for_avg=excluded.xg_for_avg,
                xg_against_avg=excluded.xg_against_avg
            """,
            (
                team_id, as_of_date_str,
                elo.get(team_id, BASE_ELO),
                attack_strength.get(team_id),
                defense_weakness.get(team_id),
                xg_for_avg.get(team_id),
                xg_against_avg.get(team_id),
            ),
        )
    conn.commit()


def fetch_team_kinds(conn):
    """Classifies each team as 'club' or 'international' based on the kind
    of league(s) its matches belong to (a team only ever plays in one)."""
    rows = conn.execute(
        """
        SELECT team_id, kind FROM (
            SELECT m.home_team_id AS team_id, l.kind AS kind FROM matches m JOIN leagues l ON m.league_id = l.id
            UNION
            SELECT m.away_team_id AS team_id, l.kind AS kind FROM matches m JOIN leagues l ON m.league_id = l.id
        )
        """
    ).fetchall()
    return dict(rows)


def print_top(label, ratings, id_to_name, team_kinds, kind, top_n, fmt):
    filtered = [(team_id, val) for team_id, val in ratings.items() if team_kinds.get(team_id) == kind]
    print(f"\n=== Top {top_n} {kind} teams by {label} ===")
    for team_id, val in sorted(filtered, key=lambda x: -x[1])[:top_n]:
        name = id_to_name.get(team_id, f"team_{team_id}")
        print(f"  {fmt(val)}  {name}")


def print_summary(conn, elo, attack_strength, defense_weakness, top_n=10):
    id_to_name = dict(conn.execute("SELECT id, name FROM teams").fetchall())
    team_kinds = fetch_team_kinds(conn)

    for kind in ("club", "international"):
        print_top("Elo", elo, id_to_name, team_kinds, kind, top_n, lambda v: f"{v:.0f}")
        print_top("attack strength", attack_strength, id_to_name, team_kinds, kind, top_n, lambda v: f"{v:.2f}x")
        print_top("defense_weakness (higher = weaker)", defense_weakness, id_to_name, team_kinds, kind, top_n, lambda v: f"{v:.2f}x")


def main():
    parser = argparse.ArgumentParser(description="Compute Elo + attack/defense ratings")
    parser.add_argument("--db", default="data/football.db")
    args = parser.parse_args()

    conn = sqlite3.connect(args.db)
    matches = fetch_finished_matches(conn)
    print(f"Processing {len(matches)} finished matches...")

    if not matches:
        print("No finished matches found — nothing to rate.")
        return

    elo = compute_elo(matches)

    latest_date_str = max(m[0] for m in matches)
    as_of_date = parse_date(latest_date_str)
    attack_strength, defense_weakness, xg_for_avg, xg_against_avg, global_avg = compute_attack_defense(matches, as_of_date)

    save_ratings(conn, elo, attack_strength, defense_weakness, xg_for_avg, xg_against_avg, latest_date_str)

    metadata_path = args.db.rsplit(".", 1)[0] + "_model_metadata.json"
    with open(metadata_path, "w") as f:
        json.dump({"global_avg_for": global_avg, "as_of_date": latest_date_str}, f)

    print(f"\nRatings saved as of {latest_date_str} (global avg goals/match baseline: {global_avg:.2f})")
    print(f"Metadata written to {metadata_path}")
    print_summary(conn, elo, attack_strength, defense_weakness)

    conn.close()


if __name__ == "__main__":
    main()