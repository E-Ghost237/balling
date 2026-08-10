"""
Step 5 of the pipeline (evaluation): walk-forward backtest of the actual
prediction pipeline (rating_engine's Elo/attack-defense + simulate.py's
lambda + Monte Carlo model) against real historical results.

This exists so future modeling changes — a new metric, a re-tuned decay
half-life, a different home-advantage value — can be judged by whether
they actually improve prediction accuracy, instead of being added because
they sound like they should help. Nothing in modeling/ currently measures
that for match outcomes (xg_model.py's log-loss/Brier only scores
individual shots, not W/D/L or scorelines).

Walk-forward, no data leakage: each match is predicted using ONLY Elo and
attack/defense figures built from STRICTLY EARLIER matches — never the
"as of latest" ratings rating_engine.py normally saves, which already
know how every team's season actually went. Concretely: for each match,
in chronological order, we read a team's current rating state, use it to
predict this match, and only THEN fold this match's real result into that
state — exactly the information a prediction made on that real date would
have had.

Runs in a single O(n) pass rather than recomputing attack/defense from
scratch per match (which is how rating_engine.py normally works, and
would be O(n^2) — with 80k+ matches in this DB, that's not viable). The
trick: rating_engine's decay weight is
    exp(-decay_lambda * (as_of_date - match_date).days)
  = exp(-decay_lambda * as_of_ordinal) * exp(decay_lambda * match_date_ordinal)
and attack/defense is a RATIO of two such weighted sums, so the
exp(-decay_lambda * as_of_ordinal) factor is common to numerator and
denominator and cancels out — meaning each team's running weighted sums
can be accumulated incrementally as matches happen, with no dependency on
which future date you'll eventually ask "what were this team's numbers
as of X" for. (Reference-date-shifted to avoid float overflow over a
century of matches — doesn't change the math, just keeps the exponents a
sane size.)

Usage:
    python backtest.py --db football.db
    python backtest.py --db football.db --since 2023-07-01 --n-sims 3000
    python backtest.py --db football.db --out backtest_results.csv
"""

import argparse
import csv
import math
import sqlite3
from collections import defaultdict
from datetime import datetime, timedelta

from rating_engine import (
    BASE_ELO,
    BASE_K,
    BLEND_GOALS_WEIGHT,
    DECAY_HALF_LIFE_DAYS,
    MOMENTUM_HALF_LIFE_DAYS,
    ON_TARGET_RESULTS,
    POINTS_DRAW,
    POINTS_WIN,
    expected_score,
    goal_diff_multiplier,
    league_weight,
    match_outcome_probs,
    parse_date,
)
from simulate import compute_lambdas, simulate_match, summarize_simulation

# Which of the optional signals (see simulate.compute_lambdas) to feed the
# model in a given run — this is exactly what lets --features be used to
# A/B a signal against the plain baseline model on the same data. All of
# them by default; pass e.g. --features elo to isolate the current model
# with none of the new nudges, as a control run.
ALL_FEATURES = {"ppg", "xppg", "momentum", "shot_diff"}

# Skip predicting for a team before it has at least this many prior
# matches — too little history to produce a meaningful rating either way.
MIN_PRIOR_MATCHES = 5

# Backtest-only: fewer sims than the live product's 20k, since we're
# averaging log-loss/Brier over thousands of matches anyway — per-match
# noise washes out in the aggregate. Override with --n-sims for a slower,
# less noisy run.
BACKTEST_N_SIMS = 3000

RESULT_CLASSES = ("H", "D", "A")


def fetch_all_finished_matches(conn: sqlite3.Connection):
    return conn.execute(
        """
        SELECT m.id, m.match_date, m.home_team_id, m.away_team_id,
               m.home_goals, m.away_goals, m.is_neutral_venue,
               COALESCE(m.home_xg_model, m.home_xg) AS home_xg,
               COALESCE(m.away_xg_model, m.away_xg) AS away_xg,
               l.name
        FROM matches m
        JOIN leagues l ON m.league_id = l.id
        WHERE m.status = 'finished' AND m.home_goals IS NOT NULL AND m.away_goals IS NOT NULL
        ORDER BY m.match_date ASC
        """
    ).fetchall()


def fetch_shot_counts(conn: sqlite3.Connection):
    """{(match_id, team_id): (shots, shots_on_target)} — mirrors
    rating_engine.fetch_shot_counts exactly; duplicated rather than
    imported since it's a two-line query, and importing it would suggest
    a coupling to rating_engine's DB connection that doesn't exist."""
    rows = conn.execute("SELECT match_id, team_id, result FROM shots").fetchall()
    counts = defaultdict(lambda: [0, 0])
    for match_id, team_id, result in rows:
        key = (match_id, team_id)
        counts[key][0] += 1
        if result in ON_TARGET_RESULTS:
            counts[key][1] += 1
    return counts


def run_backtest(matches, n_sims: int, since: datetime | None, rng,
                  features: set[str] = frozenset(ALL_FEATURES), shot_counts: dict | None = None):
    """Single chronological pass. Team rating state is built up over ALL
    matches (so ratings are properly "warmed up" even for teams whose
    history predates `since`), but a match only gets scored into the
    returned results if its date is >= `since` — that's what keeps the
    reported metrics focused on a recent, representative window without
    throwing away the warm-up signal from older matches.

    `features` controls which of simulate.compute_lambdas' optional
    nudges get fed real values (vs None, i.e. "off") — this is the A/B
    testing knob: run once with the full set, once with a subset or none,
    and compare the two reports."""
    if not matches:
        return []
    shot_counts = shot_counts or {}

    decay_lambda = math.log(2) / DECAY_HALF_LIFE_DAYS
    momentum_decay_lambda = math.log(2) / MOMENTUM_HALF_LIFE_DAYS
    # Anchored to the LAST (most recent) match, not the first: every
    # exponent is then <= 0, so decay_key <= 1 and only ever shrinks for
    # older matches — no overflow risk regardless of how steep the decay
    # is or how long the match history spans (anchoring to the first match
    # instead overflows float64 for the 30-day momentum half-life over
    # this DB's 110-year span: exp(~928) vs a safe exp(<=0)).
    reference_ordinal = parse_date(matches[-1][1]).toordinal()

    elo = defaultdict(lambda: BASE_ELO)
    s_for = defaultdict(float)
    s_against = defaultdict(float)
    s_weight = defaultdict(float)
    match_count = defaultdict(int)
    global_s_for = 0.0
    global_s_weight = 0.0

    # PPG / xPPG: same decay as attack/defense above.
    points_sum = defaultdict(float)
    xpoints_sum = defaultdict(float)
    # Momentum: a second, faster-decaying performance accumulator — see
    # rating_engine.compute_form_metrics for the non-walk-forward version
    # of this same idea.
    perf_sum_fast = defaultdict(float)
    weight_sum_fast = defaultdict(float)
    # Shot/SOT diff: only ever non-empty where Understat shot data exists.
    shot_diff_sum = defaultdict(float)
    shot_weight_sum = defaultdict(float)

    # Walk-forward baseline: the plain historical H/D/A rate seen so far,
    # with no team-specific information at all — the bar any "real" model
    # needs to clear to be worth the complexity.
    baseline_counts = {"H": 0, "D": 0, "A": 0}
    baseline_total = 0

    results = []

    for match_id, date_str, home_id, away_id, hg, ag, is_neutral, hxg, axg, league_name in matches:
        d = parse_date(date_str)
        lw = league_weight(league_name)
        decay_key = math.exp(decay_lambda * (d.toordinal() - reference_ordinal))
        decay_key_fast = math.exp(momentum_decay_lambda * (d.toordinal() - reference_ordinal))

        has_prior_data = (
            match_count[home_id] >= MIN_PRIOR_MATCHES
            and match_count[away_id] >= MIN_PRIOR_MATCHES
            and global_s_weight > 0
        )
        in_report_window = since is None or d >= since

        if has_prior_data and in_report_window:
            global_avg_for = global_s_for / global_s_weight
            home_attack = (s_for[home_id] / s_weight[home_id]) / global_avg_for
            home_defense = (s_against[home_id] / s_weight[home_id]) / global_avg_for
            away_attack = (s_for[away_id] / s_weight[away_id]) / global_avg_for
            away_defense = (s_against[away_id] / s_weight[away_id]) / global_avg_for

            extra = {}
            if "ppg" in features:
                extra["ppg_home"] = points_sum[home_id] / s_weight[home_id]
                extra["ppg_away"] = points_sum[away_id] / s_weight[away_id]
            if "xppg" in features:
                extra["xppg_home"] = xpoints_sum[home_id] / s_weight[home_id]
                extra["xppg_away"] = xpoints_sum[away_id] / s_weight[away_id]
            has_momentum_data = (
                "momentum" in features
                and weight_sum_fast[home_id] > 0
                and weight_sum_fast[away_id] > 0
            )
            if has_momentum_data:
                home_fast = perf_sum_fast[home_id] / weight_sum_fast[home_id]
                away_fast = perf_sum_fast[away_id] / weight_sum_fast[away_id]
                home_slow = s_for[home_id] / s_weight[home_id]
                away_slow = s_for[away_id] / s_weight[away_id]
                if home_slow > 0 and away_slow > 0:
                    extra["momentum_home"] = home_fast / home_slow
                    extra["momentum_away"] = away_fast / away_slow
            has_shot_data = (
                "shot_diff" in features
                and shot_weight_sum[home_id] > 0
                and shot_weight_sum[away_id] > 0
            )
            if has_shot_data:
                extra["shot_diff_home"] = shot_diff_sum[home_id] / shot_weight_sum[home_id]
                extra["shot_diff_away"] = shot_diff_sum[away_id] / shot_weight_sum[away_id]

            lambda_home, lambda_away = compute_lambdas(
                home_attack, away_defense, away_attack, home_defense,
                elo[home_id], elo[away_id], global_avg_for, neutral=bool(is_neutral),
                **extra,
            )
            goals_home, goals_away = simulate_match(
                lambda_home, lambda_away, elo[home_id], elo[away_id], n_sims=n_sims, rng=rng
            )
            summary = summarize_simulation(goals_home, goals_away)

            actual = "H" if hg > ag else ("A" if hg < ag else "D")
            baseline_probs = (
                {k: baseline_counts[k] / baseline_total for k in RESULT_CLASSES}
                if baseline_total > 0
                else {"H": 1 / 3, "D": 1 / 3, "A": 1 / 3}
            )

            results.append({
                "match_id": match_id, "date": date_str, "league": league_name,
                "home_id": home_id, "away_id": away_id,
                "prob_home_win": summary["prob_home_win"], "prob_draw": summary["prob_draw"],
                "prob_away_win": summary["prob_away_win"],
                "predicted_score": summary["most_likely_score"],
                "exp_goals_home": summary["exp_goals_home"],
                "exp_goals_away": summary["exp_goals_away"],
                "actual_home_goals": hg, "actual_away_goals": ag, "actual_result": actual,
                "baseline_prob_home_win": baseline_probs["H"],
                "baseline_prob_draw": baseline_probs["D"],
                "baseline_prob_away_win": baseline_probs["A"],
            })

        # --- only now fold this match's real result into the state, so
        # the NEXT match's prediction can see it but this one couldn't ---
        we = expected_score(elo[home_id], elo[away_id])
        w_home = 1.0 if hg > ag else (0.0 if hg < ag else 0.5)
        g = goal_diff_multiplier(hg - ag)
        k = BASE_K * lw
        delta = k * g * (w_home - we)
        elo[home_id] += delta
        elo[away_id] -= delta

        if hxg is not None and axg is not None:
            home_perf = BLEND_GOALS_WEIGHT * hg + (1 - BLEND_GOALS_WEIGHT) * hxg
            away_perf = BLEND_GOALS_WEIGHT * ag + (1 - BLEND_GOALS_WEIGHT) * axg
        else:
            home_perf = float(hg)
            away_perf = float(ag)

        w = decay_key * lw
        s_for[home_id] += w * home_perf
        s_against[home_id] += w * away_perf
        s_weight[home_id] += w
        s_for[away_id] += w * away_perf
        s_against[away_id] += w * home_perf
        s_weight[away_id] += w
        match_count[home_id] += 1
        match_count[away_id] += 1
        global_s_for += w * (home_perf + away_perf)
        global_s_weight += w * 2

        home_points = POINTS_WIN if hg > ag else (POINTS_DRAW if hg == ag else 0)
        away_points = POINTS_WIN if ag > hg else (POINTS_DRAW if hg == ag else 0)
        points_sum[home_id] += w * home_points
        points_sum[away_id] += w * away_points
        if hxg is not None and axg is not None:
            p_home, p_draw, p_away = match_outcome_probs(max(hxg, 0.01), max(axg, 0.01))
            xpoints_sum[home_id] += w * (POINTS_WIN * p_home + POINTS_DRAW * p_draw)
            xpoints_sum[away_id] += w * (POINTS_WIN * p_away + POINTS_DRAW * p_draw)
        else:
            xpoints_sum[home_id] += w * home_points
            xpoints_sum[away_id] += w * away_points

        w_fast = decay_key_fast * lw
        perf_sum_fast[home_id] += w_fast * home_perf
        perf_sum_fast[away_id] += w_fast * away_perf
        weight_sum_fast[home_id] += w_fast
        weight_sum_fast[away_id] += w_fast

        home_shots = shot_counts.get((match_id, home_id))
        away_shots = shot_counts.get((match_id, away_id))
        if home_shots is not None and away_shots is not None:
            shot_diff_sum[home_id] += w * (home_shots[0] - away_shots[0])
            shot_diff_sum[away_id] += w * (away_shots[0] - home_shots[0])
            shot_weight_sum[home_id] += w
            shot_weight_sum[away_id] += w

        outcome = "H" if hg > ag else ("A" if hg < ag else "D")
        baseline_counts[outcome] += 1
        baseline_total += 1

    return results


# ---------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------

def _class_probs(row, prefix=""):
    return {
        "H": row[f"{prefix}prob_home_win"],
        "D": row[f"{prefix}prob_draw"],
        "A": row[f"{prefix}prob_away_win"],
    }


def log_loss(results, prefix=""):
    eps = 1e-12
    total = 0.0
    for row in results:
        p = _class_probs(row, prefix)[row["actual_result"]]
        total += -math.log(max(p, eps))
    return total / len(results) if results else float("nan")


def brier_score(results, prefix=""):
    total = 0.0
    for row in results:
        probs = _class_probs(row, prefix)
        for cls in RESULT_CLASSES:
            actual_indicator = 1.0 if row["actual_result"] == cls else 0.0
            total += (probs[cls] - actual_indicator) ** 2
    return total / len(results) if results else float("nan")


def accuracy(results, prefix=""):
    correct = 0
    for row in results:
        probs = _class_probs(row, prefix)
        predicted = max(probs, key=probs.get)
        correct += predicted == row["actual_result"]
    return correct / len(results) if results else float("nan")


def correct_scoreline_rate(results):
    hits = sum(
        1 for r in results
        if r["predicted_score"]
        == f"{min(r['actual_home_goals'], 6)}-{min(r['actual_away_goals'], 6)}"
    )
    return hits / len(results) if results else float("nan")


def calibration_table(results, buckets=10):
    """For each decile of predicted home-win probability: how often did
    the home team actually win? A well-calibrated model's "actual" column
    should track its "avg predicted" column closely — if predictions
    cluster at 60-70% and home teams in that bucket only won 40% of the
    time, the model is overconfident, not just "sometimes wrong"."""
    rows = sorted(results, key=lambda r: r["prob_home_win"])
    n = len(rows)
    table = []
    for i in range(buckets):
        lo, hi = (n * i) // buckets, (n * (i + 1)) // buckets
        chunk = rows[lo:hi]
        if not chunk:
            continue
        avg_predicted = sum(r["prob_home_win"] for r in chunk) / len(chunk)
        actual_rate = sum(1 for r in chunk if r["actual_result"] == "H") / len(chunk)
        table.append({
            "bucket": i + 1, "n": len(chunk),
            "predicted_range": (chunk[0]["prob_home_win"], chunk[-1]["prob_home_win"]),
            "avg_predicted": avg_predicted, "actual_home_win_rate": actual_rate,
        })
    return table


def per_league_breakdown(results, min_matches=30):
    by_league = defaultdict(list)
    for r in results:
        by_league[r["league"]].append(r)
    rows = [
        (league, len(rows), accuracy(rows), log_loss(rows))
        for league, rows in by_league.items()
        if len(rows) >= min_matches
    ]
    return sorted(rows, key=lambda x: -x[1])


def print_report(results):
    if not results:
        print("No matches scored — widen --since or lower MIN_PRIOR_MATCHES.")
        return

    print(f"\nScored {len(results)} matches.\n")

    model_acc, base_acc = accuracy(results), accuracy(results, "baseline_")
    model_ll, base_ll = log_loss(results), log_loss(results, "baseline_")
    model_brier, base_brier = brier_score(results), brier_score(results, "baseline_")
    print(f"{'Metric':<28}{'Model':>12}{'Baseline':>12}")
    print(f"{'accuracy (picked winner)':<28}{model_acc:>12.3f}{base_acc:>12.3f}")
    print(f"{'log loss (lower better)':<28}{model_ll:>12.3f}{base_ll:>12.3f}")
    print(f"{'Brier score (lower better)':<28}{model_brier:>12.3f}{base_brier:>12.3f}")
    print(f"{'exact scoreline hit rate':<28}{correct_scoreline_rate(results):>12.3f}{'—':>12}")

    print("\nCalibration (home-win probability deciles):")
    header = (
        f"{'bucket':<8}{'n':>6}{'predicted range':>20}"
        f"{'avg predicted':>16}{'actual H rate':>16}"
    )
    print(f"  {header}")
    for row in calibration_table(results):
        lo, hi = row["predicted_range"]
        print(
            f"  {row['bucket']:<8}{row['n']:>6}{f'{lo:.2f}-{hi:.2f}':>20}"
            f"{row['avg_predicted']:>16.3f}{row['actual_home_win_rate']:>16.3f}"
        )

    print("\nPer-league (>= 30 scored matches), by accuracy:")
    print(f"  {'league':<32}{'n':>6}{'accuracy':>10}{'log loss':>10}")
    for league, n, acc, ll in per_league_breakdown(results):
        print(f"  {league:<32}{n:>6}{acc:>10.3f}{ll:>10.3f}")


def write_csv(results, path):
    if not results:
        return
    with open(path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(results[0].keys()))
        writer.writeheader()
        writer.writerows(results)
    print(f"\nPer-match predictions written to {path}")


def main():
    parser = argparse.ArgumentParser(
        description="Walk-forward backtest of the prediction pipeline against real results"
    )
    parser.add_argument("--db", default="data/football.db")
    parser.add_argument(
        "--since", default=None,
        help="Only score matches on/after this date (YYYY-MM-DD). Ratings still warm up on "
             "everything before it. Defaults to the last 2 years.",
    )
    parser.add_argument("--n-sims", type=int, default=BACKTEST_N_SIMS)
    parser.add_argument("--out", default=None, help="Optional CSV path for per-match results")
    parser.add_argument("--seed", type=int, default=42, help="RNG seed, for reproducible runs")
    parser.add_argument(
        "--features", default="ppg,xppg,momentum,shot_diff",
        help="Comma-separated subset of ppg,xppg,momentum,shot_diff to enable — "
             "the A/B testing knob. Pass an empty string for the current model "
             "with none of them, as a control run to compare against.",
    )
    args = parser.parse_args()

    since = (
        parse_date(args.since) if args.since
        else datetime.now() - timedelta(days=365 * 2)
    )
    features = {f.strip() for f in args.features.split(",") if f.strip()}
    unknown = features - ALL_FEATURES
    if unknown:
        parser.error(f"unknown --features {sorted(unknown)}; choose from {sorted(ALL_FEATURES)}")

    conn = sqlite3.connect(args.db)
    matches = fetch_all_finished_matches(conn)
    shot_counts = fetch_shot_counts(conn) if "shot_diff" in features else {}
    print(f"Loaded {len(matches)} finished matches total; scoring those on/after {since.date()}.")
    print(f"Features enabled: {sorted(features) or '(none — control run)'}")

    import numpy as np
    rng = np.random.default_rng(args.seed)
    results = run_backtest(matches, n_sims=args.n_sims, since=since, rng=rng,
                            features=features, shot_counts=shot_counts)

    print_report(results)
    if args.out:
        write_csv(results, args.out)


if __name__ == "__main__":
    main()
