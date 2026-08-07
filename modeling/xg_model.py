"""
Step between shot collection and rating: fits our own expected-goals (xG)
model from shot-level data, instead of trusting the source's own xG figure.

Only Understat shots have coordinates today (populated via
`understat_scraper.py --with-shots` / `batch_scrape.py --with-shots`), so
this only affects matches from that source — everything else keeps
falling back to source xG or goals-only, same as before.

Model:
  - Logistic regression (plain numpy gradient descent, no sklearn
    dependency) predicting P(goal) per shot.
  - Features: distance to goal center, shot angle (subtended by the goal
    mouth), header flag, and situation dummies (penalty / set-piece /
    fast-break, with open play as the baseline).
  - Understat's x/y are normalized 0-1 over a 105m x 68m pitch, with the
    shooting team always attacking toward x=1, goal centered at
    (1, 0.5).

Usage:
    python xg_model.py --db data/football.db
"""

import argparse
import math
import sqlite3
from collections import defaultdict

import numpy as np

PITCH_LENGTH_M = 105.0
PITCH_WIDTH_M = 68.0
GOAL_WIDTH_M = 7.32

L2_REG = 1e-3
LEARNING_RATE = 0.1
N_ITERATIONS = 2000


def fetch_shots(conn):
    return conn.execute(
        """
        SELECT id, match_id, team_id, x, y, situation, body_part, result
        FROM shots
        WHERE x IS NOT NULL AND y IS NOT NULL
        """
    ).fetchall()


def build_features(rows):
    """Turns raw shot rows into a feature matrix + goal labels."""
    n = len(rows)
    X = np.zeros((n, 6))
    y = np.zeros(n)

    for i, (_id, _match_id, _team_id, x, y_coord, situation, body_part, result) in enumerate(rows):
        shot_x_m = x * PITCH_LENGTH_M
        shot_y_m = y_coord * PITCH_WIDTH_M
        goal_x_m = PITCH_LENGTH_M
        goal_y_m = PITCH_WIDTH_M / 2

        dx = goal_x_m - shot_x_m
        dy = goal_y_m - shot_y_m
        distance = math.hypot(dx, dy)

        # Angle subtended by the goal mouth from the shot location —
        # the standard xG geometry feature (bigger angle = easier chance).
        post1_y = goal_y_m - GOAL_WIDTH_M / 2
        post2_y = goal_y_m + GOAL_WIDTH_M / 2
        angle1 = math.atan2(post1_y - shot_y_m, dx)
        angle2 = math.atan2(post2_y - shot_y_m, dx)
        angle = abs(angle1 - angle2)

        is_header = 1.0 if body_part == "Head" else 0.0
        is_penalty = 1.0 if situation == "Penalty" else 0.0
        is_set_piece = 1.0 if situation in ("SetPiece", "DirectFreekick", "FromCorner") else 0.0
        is_fast_break = 1.0 if situation == "FastBreak" else 0.0

        X[i] = [distance, angle, is_header, is_penalty, is_set_piece, is_fast_break]
        y[i] = 1.0 if result == "Goal" else 0.0

    return X, y


def standardize(X):
    mean = X.mean(axis=0)
    std = X.std(axis=0)
    std[std == 0] = 1.0
    return (X - mean) / std, mean, std


def sigmoid(z):
    return 1 / (1 + np.exp(-np.clip(z, -30, 30)))


def train_logistic_regression(X, y, l2=L2_REG, lr=LEARNING_RATE, n_iter=N_ITERATIONS):
    n, d = X.shape
    X_bias = np.hstack([np.ones((n, 1)), X])
    weights = np.zeros(d + 1)

    for _ in range(n_iter):
        z = X_bias @ weights
        preds = sigmoid(z)
        grad = X_bias.T @ (preds - y) / n
        grad[1:] += l2 * weights[1:] / n  # don't regularize the bias term
        weights -= lr * grad

    return weights


def predict(X, weights):
    n = X.shape[0]
    X_bias = np.hstack([np.ones((n, 1)), X])
    return sigmoid(X_bias @ weights)


def evaluate(preds, y, source_xg=None):
    eps = 1e-9
    preds_c = np.clip(preds, eps, 1 - eps)
    log_loss = float(-np.mean(y * np.log(preds_c) + (1 - y) * np.log(1 - preds_c)))
    brier = float(np.mean((preds - y) ** 2))
    print(f"  log loss:   {log_loss:.4f}")
    print(f"  brier score: {brier:.4f}")
    print(f"  mean model xG: {preds.mean():.3f}   actual goal rate: {y.mean():.3f}")
    if source_xg is not None:
        mae = float(np.mean(np.abs(preds - source_xg)))
        corr = float(np.corrcoef(preds, source_xg)[0, 1])
        print(f"  vs source xG — MAE: {mae:.3f}, correlation: {corr:.3f}")


def save_shot_xg(conn, shot_ids, model_xg):
    conn.executemany(
        "UPDATE shots SET model_xg = ? WHERE id = ?",
        list(zip(model_xg.tolist(), shot_ids)),
    )
    conn.commit()


def aggregate_match_xg(conn):
    """Sums model_xg per team per match, writes matches.home_xg_model / away_xg_model."""
    rows = conn.execute(
        """
        SELECT s.match_id, m.home_team_id, m.away_team_id, s.team_id, SUM(s.model_xg)
        FROM shots s
        JOIN matches m ON m.id = s.match_id
        WHERE s.model_xg IS NOT NULL
        GROUP BY s.match_id, s.team_id
        """
    ).fetchall()

    match_xg = defaultdict(dict)
    for match_id, home_id, away_id, team_id, total_xg in rows:
        side = "home" if team_id == home_id else "away"
        match_xg[match_id][side] = total_xg

    for match_id, sides in match_xg.items():
        conn.execute(
            "UPDATE matches SET home_xg_model = ?, away_xg_model = ? WHERE id = ?",
            (sides.get("home"), sides.get("away"), match_id),
        )
    conn.commit()
    return len(match_xg)


def main():
    parser = argparse.ArgumentParser(description="Fit and apply our own shot-level xG model")
    parser.add_argument("--db", default="data/football.db")
    args = parser.parse_args()

    conn = sqlite3.connect(args.db)
    rows = fetch_shots(conn)
    print(f"Loaded {len(rows)} shots with coordinates.")

    if not rows:
        print("No shots found — run understat_scraper.py / batch_scrape.py with --with-shots first.")
        return

    X, y = build_features(rows)
    X_std, mean, std = standardize(X)

    weights = train_logistic_regression(X_std, y)
    preds = predict(X_std, weights)

    source_xg_rows = conn.execute(
        "SELECT xg FROM shots WHERE x IS NOT NULL AND y IS NOT NULL"
    ).fetchall()
    source_xg = np.array([r[0] if r[0] is not None else np.nan for r in source_xg_rows])
    valid_mask = ~np.isnan(source_xg)

    print("\n=== Model fit ===")
    evaluate(preds, y, source_xg=source_xg[valid_mask] if valid_mask.any() else None)

    feature_names = ["distance", "angle", "is_header", "is_penalty", "is_set_piece", "is_fast_break"]
    print("\n=== Learned weights (standardized features) ===")
    print(f"  bias: {weights[0]:.3f}")
    for name, w in zip(feature_names, weights[1:]):
        print(f"  {name}: {w:.3f}")

    shot_ids = [r[0] for r in rows]
    save_shot_xg(conn, shot_ids, preds)

    n_matches = aggregate_match_xg(conn)
    print(f"\nWrote model_xg for {len(shot_ids)} shots and aggregated home_xg_model/away_xg_model for {n_matches} matches.")

    conn.close()


if __name__ == "__main__":
    main()
