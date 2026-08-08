"""
Step 4 of the pipeline: turns a team's Elo + attack/defense ratings into
match predictions — win/draw/loss probabilities, correct-score
distribution, and expected goals — via Monte Carlo simulation.

Design (per your spec):
  - Expected goals (lambda) are PRIMARILY driven by attack/defense
    strength (Dixon-Coles style: lambda_home = global_avg * home_attack *
    away_defense * home_advantage). Elo only nudges the result slightly
    (a small multiplicative adjustment, capped either direction) — it
    does not dominate the calculation.
  - Unpredictability: each simulated match draws a random noise
    multiplier on top of the base lambda, with the noise's spread
    (sigma) INCREASING for lower-rated teams — weaker teams are more
    live for an upset because their performance swings around more.
  - 10k-30k simulations per match (n_sims argument, default 20000),
    producing win/draw/loss, the full scoreline probability matrix, the
    single most likely scoreline, and each side's expected goals.

Usage:
    python simulate.py --db football.db --home "Manchester City" --away "Arsenal"
    python simulate.py --db football.db --home "Manchester City" --away "Arsenal" --n-sims 30000
"""

import argparse
import json
import math
import sqlite3
from collections import Counter

import numpy as np

# --- Lambda calculation ---
HOME_GOAL_ADVANTAGE = 1.2  # multiplicative bump applied to the home side's base lambda

# Elo's influence is intentionally small — attack/defense strength is primary.
ELO_INFLUENCE = 0.15  # max +/-15% swing to lambda from an Elo mismatch

# --- Per-team unpredictability (variance) ---
REFERENCE_ELO = 1500
BASE_SIGMA = 0.12          # noise spread for an average-or-stronger team
MAX_EXTRA_SIGMA = 0.25     # additional spread piled on for very weak teams
ELO_SPREAD_FOR_MAX_SIGMA = 400  # Elo points below reference needed to hit max extra sigma

# --- Simulation ---
DEFAULT_N_SIMS = 20000
MAX_DISPLAY_GOALS = 6  # scorelines above this get bucketed as "6+" for display purposes

# --- Additional markets (BTTS / Over-Under / Handicap) ---
OU_LINES = [0.5, 1.5, 2.5, 3.5, 4.5, 5.5]
HANDICAP_LINES = [x / 2 for x in range(-6, 7)]  # -3.0 .. 3.0 in 0.5 steps

# --- Best picks ---
BEST_PICK_THRESHOLD = 0.60  # only surface selections we give at least 60% to
BEST_PICK_TOP_N = 5


def team_sigma(elo: float) -> float:
    """Lower Elo -> higher sigma -> more variance -> more upset-prone in simulation."""
    deficit = max(0.0, REFERENCE_ELO - elo)
    extra = MAX_EXTRA_SIGMA * min(1.0, deficit / ELO_SPREAD_FOR_MAX_SIGMA)
    return BASE_SIGMA + extra


def elo_adjustment_factor(elo_for: float, elo_against: float) -> float:
    """Small multiplicative nudge based on Elo gap — capped at +/-ELO_INFLUENCE."""
    diff = elo_for - elo_against
    return 1 + ELO_INFLUENCE * math.tanh(diff / 400)


def compute_lambdas(home_attack, away_defense, away_attack, home_defense,
                     elo_home, elo_away, global_avg_for, neutral=False):
    home_advantage = 1.0 if neutral else HOME_GOAL_ADVANTAGE
    base_home = global_avg_for * home_attack * away_defense * home_advantage
    base_away = global_avg_for * away_attack * home_defense

    lambda_home = base_home * elo_adjustment_factor(elo_home, elo_away)
    lambda_away = base_away * elo_adjustment_factor(elo_away, elo_home)

    return lambda_home, lambda_away


def simulate_match(lambda_home, lambda_away, elo_home, elo_away,
                    n_sims=DEFAULT_N_SIMS, rng=None):
    rng = rng or np.random.default_rng()

    sigma_home = team_sigma(elo_home)
    sigma_away = team_sigma(elo_away)

    # Lognormal noise with mean 1 (mu = -sigma^2/2 makes E[exp(N(mu,sigma))] = 1),
    # so the noise multiplies lambda without systematically inflating or
    # deflating it on average — only adding spread.
    noise_home = rng.lognormal(mean=-0.5 * sigma_home**2, sigma=sigma_home, size=n_sims)
    noise_away = rng.lognormal(mean=-0.5 * sigma_away**2, sigma=sigma_away, size=n_sims)

    sim_lambda_home = lambda_home * noise_home
    sim_lambda_away = lambda_away * noise_away

    goals_home = rng.poisson(sim_lambda_home)
    goals_away = rng.poisson(sim_lambda_away)

    return goals_home, goals_away


def summarize_simulation(goals_home, goals_away):
    n = len(goals_home)

    prob_home_win = float(np.mean(goals_home > goals_away))
    prob_draw = float(np.mean(goals_home == goals_away))
    prob_away_win = float(np.mean(goals_home < goals_away))

    capped_home = np.minimum(goals_home, MAX_DISPLAY_GOALS)
    capped_away = np.minimum(goals_away, MAX_DISPLAY_GOALS)

    def format_scoreline(h, a):
        h_label = f"{h}{'+' if h == MAX_DISPLAY_GOALS else ''}"
        a_label = f"{a}{'+' if a == MAX_DISPLAY_GOALS else ''}"
        return f"{h_label}-{a_label}"

    scoreline_counts = Counter(zip(capped_home.tolist(), capped_away.tolist()))
    scoreline_probs = {format_scoreline(h, a): count / n for (h, a), count in scoreline_counts.items()}

    most_likely_key = max(scoreline_counts, key=scoreline_counts.get)
    most_likely_score = format_scoreline(*most_likely_key)

    return {
        "prob_home_win": prob_home_win,
        "prob_draw": prob_draw,
        "prob_away_win": prob_away_win,
        "exp_goals_home": float(np.mean(goals_home)),
        "exp_goals_away": float(np.mean(goals_away)),
        "most_likely_score": most_likely_score,
        "scoreline_probs": scoreline_probs,
        "n_sims": n,
    }


def compute_btts(goals_home, goals_away):
    prob_yes = float(np.mean((goals_home >= 1) & (goals_away >= 1)))
    return {"yes": prob_yes, "no": 1 - prob_yes}


def compute_over_under(goals_home, goals_away, lines=OU_LINES):
    total = goals_home + goals_away
    return {
        line: {"over": float(np.mean(total > line)), "under": float(np.mean(total < line))}
        for line in lines
    }


def compute_handicap(goals_home, goals_away, lines=HANDICAP_LINES):
    """Asian-style handicap applied to the home side: a line of -1.5 means
    home must win by 2+ to cover. Whole-number lines can push (exact tie
    after adjustment); half lines can't, since goals are integers."""
    result = {}
    for line in lines:
        adjusted = goals_home + line - goals_away
        result[line] = {
            "home": float(np.mean(adjusted > 0)),
            "away": float(np.mean(adjusted < 0)),
            "push": float(np.mean(adjusted == 0)),
        }
    return result


def closest_to_even(probs_by_line, side_key):
    """The line whose `side_key` probability is nearest 50/50 — the most
    balanced, and therefore most informative, line to headline."""
    return min(probs_by_line.items(), key=lambda kv: abs(kv[1][side_key] - 0.5))[0]


def compute_markets(goals_home, goals_away):
    over_under = compute_over_under(goals_home, goals_away)
    handicap = compute_handicap(goals_home, goals_away)
    return {
        "btts": compute_btts(goals_home, goals_away),
        "over_under": over_under,
        "handicap": handicap,
        "fair_ou_line": closest_to_even(over_under, "over"),
        "fair_handicap_line": closest_to_even(handicap, "home"),
    }


def compute_best_picks(summary, markets, home_team, away_team,
                        threshold=BEST_PICK_THRESHOLD, top_n=BEST_PICK_TOP_N):
    """Ranks one representative selection per market — match result, BTTS,
    the most balanced total-goals line, the most balanced handicap line —
    and keeps only the ones confident enough to call a 'pick'."""
    fair_ou = markets["fair_ou_line"]
    fair_hc = markets["fair_handicap_line"]
    ou = markets["over_under"][fair_ou]
    hc = markets["handicap"][fair_hc]

    candidates = [
        ("Match result", f"{home_team} win", summary["prob_home_win"]),
        ("Match result", "Draw", summary["prob_draw"]),
        ("Match result", f"{away_team} win", summary["prob_away_win"]),
        ("BTTS", "Yes", markets["btts"]["yes"]),
        ("BTTS", "No", markets["btts"]["no"]),
        ("Total goals", f"Over {fair_ou}", ou["over"]),
        ("Total goals", f"Under {fair_ou}", ou["under"]),
        ("Handicap", f"{home_team} {fair_hc:+g}", hc["home"]),
        ("Handicap", f"{away_team} {-fair_hc:+g}", hc["away"]),
    ]

    qualifying = sorted((c for c in candidates if c[2] >= threshold), key=lambda c: -c[2])
    return [{"market": m, "selection": s, "probability": p} for m, s, p in qualifying[:top_n]]


# ---------------------------------------------------------------------
# DB integration
# ---------------------------------------------------------------------

def get_team_id(conn, name):
    row = conn.execute("SELECT id FROM teams WHERE name = ?", (name,)).fetchone()
    if row:
        return row[0]
    row = conn.execute("SELECT id FROM teams WHERE name LIKE ?", (f"%{name}%",)).fetchone()
    return row[0] if row else None


def get_latest_rating(conn, team_id):
    row = conn.execute(
        """
        SELECT elo, attack_strength, defense_weakness
        FROM team_ratings
        WHERE team_id = ?
        ORDER BY as_of_date DESC
        LIMIT 1
        """,
        (team_id,),
    ).fetchone()
    if not row:
        return None
    return {"elo": row[0], "attack_strength": row[1], "defense_weakness": row[2]}


def load_global_avg(db_path):
    metadata_path = db_path.rsplit(".", 1)[0] + "_model_metadata.json"
    try:
        with open(metadata_path) as f:
            data = json.load(f)
        return data["global_avg_for"]
    except (FileNotFoundError, KeyError):
        return None


def save_prediction(conn, home_team_id, away_team_id, lambda_home, lambda_away, summary, confidence_flag, markets=None, best_picks=None):
    import json
    from datetime import datetime, timezone
    extra_json = json.dumps({"markets": markets, "best_picks": best_picks}) if markets is not None else None
    cur = conn.execute(
        """
        INSERT INTO predictions (
            generated_at, home_team_id, away_team_id, lambda_home, lambda_away,
            n_simulations, prob_home_win, prob_draw, prob_away_win,
            most_likely_score, confidence_flag, extra_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            datetime.now(timezone.utc).isoformat(), home_team_id, away_team_id,
            lambda_home, lambda_away, summary["n_sims"],
            summary["prob_home_win"], summary["prob_draw"], summary["prob_away_win"],
            summary["most_likely_score"], confidence_flag, extra_json,
        ),
    )
    prediction_id = cur.lastrowid

    for scoreline, prob in summary["scoreline_probs"].items():
        h, a = scoreline.replace("+", "").split("-")
        conn.execute(
            "INSERT INTO prediction_scorelines (prediction_id, home_goals, away_goals, probability) VALUES (?, ?, ?, ?)",
            (prediction_id, int(h), int(a), prob),
        )
    conn.commit()
    return prediction_id


def predict_match(conn, db_path, home_name, away_name, n_sims=DEFAULT_N_SIMS, save=True, neutral=False):
    home_id = get_team_id(conn, home_name)
    away_id = get_team_id(conn, away_name)
    if home_id is None:
        raise ValueError(f"No team found matching '{home_name}'")
    if away_id is None:
        raise ValueError(f"No team found matching '{away_name}'")

    home_rating = get_latest_rating(conn, home_id)
    away_rating = get_latest_rating(conn, away_id)
    if home_rating is None:
        raise ValueError(f"No ratings found for team id {home_id} — run rating_engine.py first")
    if away_rating is None:
        raise ValueError(f"No ratings found for team id {away_id} — run rating_engine.py first")

    global_avg = load_global_avg(db_path)
    if global_avg is None:
        raise ValueError(
            f"Could not find model metadata for {db_path} — run rating_engine.py first "
            f"(it writes a *_model_metadata.json file alongside the database)."
        )

    lambda_home, lambda_away = compute_lambdas(
        home_rating["attack_strength"], away_rating["defense_weakness"],
        away_rating["attack_strength"], home_rating["defense_weakness"],
        home_rating["elo"], away_rating["elo"], global_avg, neutral=neutral,
    )

    goals_home, goals_away = simulate_match(
        lambda_home, lambda_away, home_rating["elo"], away_rating["elo"], n_sims=n_sims
    )
    summary = summarize_simulation(goals_home, goals_away)
    markets = compute_markets(goals_home, goals_away)
    best_picks = compute_best_picks(summary, markets, home_name, away_name)

    confidence_flag = "xg_based"

    prediction_id = None
    if save:
        prediction_id = save_prediction(
            conn, home_id, away_id, lambda_home, lambda_away, summary, confidence_flag,
            markets=markets, best_picks=best_picks,
        )

    return {
        "home_team": home_name, "away_team": away_name,
        "home_elo": home_rating["elo"], "away_elo": away_rating["elo"],
        "lambda_home": lambda_home, "lambda_away": lambda_away,
        "prediction_id": prediction_id,
        "markets": markets,
        "best_picks": best_picks,
        **summary,
    }


def print_prediction(result):
    print(f"\n{result['home_team']} (Elo {result['home_elo']:.0f}) vs {result['away_team']} (Elo {result['away_elo']:.0f})")
    print(f"Expected goals: {result['lambda_home']:.2f} - {result['lambda_away']:.2f}")
    print(f"\nWin/Draw/Loss:")
    print(f"  {result['home_team']} win: {result['prob_home_win']*100:.1f}%")
    print(f"  Draw: {result['prob_draw']*100:.1f}%")
    print(f"  {result['away_team']} win: {result['prob_away_win']*100:.1f}%")
    print(f"\nMost likely scoreline: {result['most_likely_score']}")
    print(f"\nTop 8 scorelines by probability:")
    for scoreline, prob in sorted(result["scoreline_probs"].items(), key=lambda x: -x[1])[:8]:
        print(f"  {scoreline}: {prob*100:.1f}%")

    btts = result["markets"]["btts"]
    print(f"\nBTTS: Yes {btts['yes']*100:.1f}% / No {btts['no']*100:.1f}%")

    if result["best_picks"]:
        print(f"\nBest picks (>= {BEST_PICK_THRESHOLD*100:.0f}% confidence):")
        for pick in result["best_picks"]:
            print(f"  [{pick['market']}] {pick['selection']}: {pick['probability']*100:.1f}%")
    else:
        print(f"\nNo selection cleared the {BEST_PICK_THRESHOLD*100:.0f}% confidence bar for this match.")

    print(f"\n(based on {result['n_sims']} simulations)")


def main():
    parser = argparse.ArgumentParser(description="Simulate a match and predict the outcome")
    parser.add_argument("--db", default="data/football.db")
    parser.add_argument("--home", required=True, help="Home team name (exact or partial match)")
    parser.add_argument("--away", required=True, help="Away team name (exact or partial match)")
    parser.add_argument("--n-sims", type=int, default=DEFAULT_N_SIMS)
    parser.add_argument("--no-save", action="store_true", help="Don't write the prediction to the database")
    parser.add_argument("--neutral", action="store_true", help="Neutral venue (no home advantage) — e.g. a cup final or World Cup group match")
    args = parser.parse_args()

    conn = sqlite3.connect(args.db)
    result = predict_match(conn, args.db, args.home, args.away, n_sims=args.n_sims, save=not args.no_save, neutral=args.neutral)
    print_prediction(result)
    conn.close()


if __name__ == "__main__":
    main()
