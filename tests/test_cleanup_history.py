import sqlite3
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from sqlalchemy import select

from webapp.cleanup_history import main as run_cleanup
from webapp.config import get_settings
from webapp.db import get_engine
from webapp.models import UsageLog, User
from webapp.security import hash_password


async def _create_user(login: str) -> object:
    user_id = uuid4()
    async with get_engine().begin() as connection:
        await connection.execute(
            User.__table__.insert().values(
                id=user_id,
                login=login,
                password_hash=hash_password("hunter22"),
                is_admin=False,
                created_at=datetime.now(UTC),
            )
        )
    return user_id


def _insert_fake_prediction() -> int:
    conn = sqlite3.connect(get_settings().football_db_path)
    try:
        home_id, away_id = conn.execute("SELECT id FROM teams LIMIT 2").fetchall()[0:2]
        cur = conn.execute(
            """
            INSERT INTO predictions (
                generated_at, home_team_id, away_team_id, lambda_home, lambda_away,
                n_simulations, prob_home_win, prob_draw, prob_away_win,
                most_likely_score, confidence_flag
            ) VALUES (?, ?, ?, 1.5, 1.2, 1000, 0.4, 0.3, 0.3, '1-1', 'xg_based')
            """,
            (datetime.now(UTC).isoformat(), home_id[0], away_id[0]),
        )
        prediction_id = cur.lastrowid
        conn.execute(
            "INSERT INTO prediction_scorelines "
            "(prediction_id, home_goals, away_goals, probability) VALUES (?, 1, 1, 0.2)",
            (prediction_id,),
        )
        conn.commit()
        return prediction_id
    finally:
        conn.close()


def _prediction_exists(prediction_id: int) -> bool:
    conn = sqlite3.connect(get_settings().football_db_path)
    try:
        return conn.execute(
            "SELECT 1 FROM predictions WHERE id = ?", (prediction_id,)
        ).fetchone() is not None
    finally:
        conn.close()


async def test_cleanup_purges_stale_history_but_keeps_recent() -> None:
    user_id = await _create_user("cleanup-user")
    stale_prediction_id = _insert_fake_prediction()
    recent_prediction_id = _insert_fake_prediction()

    now = datetime.now(UTC)
    async with get_engine().begin() as connection:
        await connection.execute(
            UsageLog.__table__.insert().values(
                id=uuid4(), user_id=user_id, home_team="A", away_team="B", neutral=False,
                simulated_at=now - timedelta(days=15), prediction_id=stale_prediction_id,
            )
        )
        await connection.execute(
            UsageLog.__table__.insert().values(
                id=uuid4(), user_id=user_id, home_team="C", away_team="D", neutral=False,
                simulated_at=now - timedelta(days=1), prediction_id=recent_prediction_id,
            )
        )

    await run_cleanup()

    async with get_engine().begin() as connection:
        result = await connection.execute(
            select(UsageLog.prediction_id).where(UsageLog.user_id == user_id)
        )
        remaining = {row[0] for row in result.all()}
    assert remaining == {recent_prediction_id}
    assert not _prediction_exists(stale_prediction_id)
    assert _prediction_exists(recent_prediction_id)
