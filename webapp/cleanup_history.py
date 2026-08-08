"""Purges simulation history past the retention window, run daily via host
cron (see infra notes) — usage_log rows are cheap, but each one anchors a
SQLite predictions row plus up to ~49 prediction_scorelines rows, which is
the actual disk cost this bounds."""

import asyncio
import sqlite3
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, select

from webapp.config import get_settings
from webapp.db import get_engine, reset_engine
from webapp.models import UsageLog

RETENTION_DAYS = 14


async def _purge_postgres(cutoff: datetime) -> list[int]:
    engine = get_engine()
    async with engine.begin() as connection:
        result = await connection.execute(
            select(UsageLog.prediction_id).where(
                UsageLog.simulated_at < cutoff, UsageLog.prediction_id.is_not(None)
            )
        )
        prediction_ids = [row[0] for row in result.all()]
        await connection.execute(delete(UsageLog).where(UsageLog.simulated_at < cutoff))
    return prediction_ids


def _purge_sqlite(db_path: str, prediction_ids: list[int]) -> None:
    if not prediction_ids:
        return
    conn = sqlite3.connect(db_path)
    try:
        placeholders = ",".join("?" * len(prediction_ids))
        conn.execute(
            f"DELETE FROM prediction_scorelines WHERE prediction_id IN ({placeholders})",
            prediction_ids,
        )
        conn.execute(f"DELETE FROM predictions WHERE id IN ({placeholders})", prediction_ids)
        conn.commit()
    finally:
        conn.close()


async def main() -> None:
    cutoff = datetime.now(UTC) - timedelta(days=RETENTION_DAYS)
    prediction_ids = await _purge_postgres(cutoff)
    _purge_sqlite(get_settings().football_db_path, prediction_ids)
    print(f"Purged {len(prediction_ids)} prediction(s) older than {RETENTION_DAYS} days.")
    await reset_engine()


if __name__ == "__main__":
    asyncio.run(main())
