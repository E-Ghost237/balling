"""Public accuracy tracking — grades fixture-based predictions (see
models.Prediction.fixture_date) against real results once the match has
finished. Backs both the public /accuracy log and the landing page's
trust stats — deliberately the same numbers in both places.

Only predictions tied to a real fixture (fixture_date set) are graded —
a hypothetical "Friendly" matchup has no real-world result to compare
against, and including it would either skip it silently (fine) or, worse,
tempt someone into fabricating a "result" for it later. Every graded
prediction is shown, wins and misses alike — a curated highlight reel
would defeat the entire point of a trust-building track record."""

import sqlite3
from datetime import UTC, datetime, timedelta

# 2026/2027 season start — the landing-page trust stats are scoped to the
# current season on purpose (a stray earlier-season fixture, or a future
# season once this rolls over, shouldn't quietly blend into "this
# season's" won/lost count). In practice every graded prediction today is
# already after this date anyway (the fixtures feature only just shipped),
# but the cutoff is explicit so it doesn't silently start including the
# wrong season once results accumulate across a rollover.
CURRENT_SEASON_START = datetime(2026, 7, 1, tzinfo=UTC)

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncConnection

from webapp.models import Prediction


def _actual_result(db_path: str, home: str, away: str, fixture_date: datetime) -> tuple[int, int] | None:
    """Looks up the real score by team name + a +/-1 day window around the
    fixture date (not a stored SQLite row id — team/match data crosses the
    Postgres/SQLite boundary by name everywhere else in this codebase, for
    the same data/football.db-can-be-rebuilt resilience reason documented
    on models.Prediction itself). Returns None if the match hasn't been
    recorded as finished yet."""
    conn = sqlite3.connect(db_path)
    try:
        window_start = (fixture_date - timedelta(days=1)).strftime("%Y-%m-%d")
        window_end = (fixture_date + timedelta(days=1)).strftime("%Y-%m-%d")
        row = conn.execute(
            """
            SELECT m.home_goals, m.away_goals
            FROM matches m
            JOIN teams ht ON ht.id = m.home_team_id
            JOIN teams at ON at.id = m.away_team_id
            WHERE ht.name = ? AND at.name = ? AND m.status = 'finished'
              AND m.match_date BETWEEN ? AND ?
            ORDER BY m.match_date DESC
            LIMIT 1
            """,
            (home, away, window_start, window_end),
        ).fetchone()
    finally:
        conn.close()
    if row is None or row[0] is None or row[1] is None:
        return None
    return row[0], row[1]


def _predicted_outcome(row: dict) -> str:
    probs = {"H": row["prob_home_win"], "D": row["prob_draw"], "A": row["prob_away_win"]}
    return max(probs, key=probs.get)


def _actual_outcome(home_goals: int, away_goals: int) -> str:
    if home_goals > away_goals:
        return "H"
    if home_goals < away_goals:
        return "A"
    return "D"


async def graded_predictions(
    connection: AsyncConnection, db_path: str, limit: int = 100
) -> list[dict]:
    """Every *distinct real fixture* with at least one prediction, paired
    with its real result where the match is recorded as finished — most
    recent first. Deliberately one entry per fixture, not one per
    prediction: many different users can click the same listed fixture,
    each generating their own Prediction row, but the public log is a
    track record of our calls, not a per-user history — showing the same
    match five times because five people simulated it would be noise, not
    signal. The earliest submission for a given fixture is kept (it's the
    one that was actually "the call" before kickoff, for any of the
    others that came later). Predictions for a match not finished yet
    (kicked off but still in progress, or the result hasn't synced down
    from the daily fetch) come back with settled=False rather than being
    skipped, so the log doesn't silently hide anything."""
    rows = (
        (
            await connection.execute(
                select(Prediction)
                .where(Prediction.fixture_date.is_not(None))
                .where(Prediction.fixture_date >= CURRENT_SEASON_START)
                .where(Prediction.fixture_date < datetime.now(UTC))
                .order_by(Prediction.fixture_date.desc(), Prediction.generated_at.asc())
                .limit(2000)  # raw-row safety cap; dedup + `limit` below controls what's shown
            )
        )
        .mappings()
        .all()
    )

    graded = []
    seen_fixtures: set[tuple] = set()
    for row in rows:
        fixture_key = (row["home_team"], row["away_team"], row["fixture_date"])
        if fixture_key in seen_fixtures:
            continue
        seen_fixtures.add(fixture_key)
        if len(graded) >= limit:
            break

        actual = _actual_result(db_path, row["home_team"], row["away_team"], row["fixture_date"])
        entry = {
            "fixture_date": row["fixture_date"],
            "home_team": row["home_team"],
            "away_team": row["away_team"],
            "predicted_score": row["most_likely_score"],
            "predicted_outcome": _predicted_outcome(row),
            "settled": actual is not None,
        }
        if actual is not None:
            home_goals, away_goals = actual
            entry["actual_score"] = f"{home_goals}-{away_goals}"
            entry["actual_outcome"] = _actual_outcome(home_goals, away_goals)
            entry["outcome_correct"] = entry["predicted_outcome"] == entry["actual_outcome"]
            entry["score_correct"] = entry["predicted_score"] == entry["actual_score"]
        graded.append(entry)
    return graded


def accuracy_summary(graded: list[dict]) -> dict:
    """graded is already scoped to the current season (CURRENT_SEASON_START,
    applied in graded_predictions' query) and already deduped to one entry
    per real fixture — won/lost here are plain counts of that same set,
    not per-user prediction counts."""
    settled = [g for g in graded if g["settled"]]
    if not settled:
        return {
            "total": 0, "outcome_correct": 0, "outcome_pct": None,
            "score_correct": 0, "score_pct": None, "won": 0, "lost": 0,
        }
    total = len(settled)
    outcome_correct = sum(1 for g in settled if g["outcome_correct"])
    score_correct = sum(1 for g in settled if g["score_correct"])
    return {
        "total": total,
        "outcome_correct": outcome_correct,
        "outcome_pct": round(outcome_correct / total * 100, 1),
        "won": outcome_correct,
        "lost": total - outcome_correct,
        "score_correct": score_correct,
        "score_pct": round(score_correct / total * 100, 1),
    }
