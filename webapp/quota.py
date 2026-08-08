import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncConnection

from webapp.models import UsageLog

# Warn once a subscription has used this fraction of its own quota_limit.
# Proportional rather than a fixed number since limits now range from 150
# (monthly) to 3,000 (yearly) depending on plan (see webapp/plans.py).
QUOTA_WARNING_RATIO = 0.85


@dataclass(frozen=True, slots=True)
class QuotaCheck:
    allowed: bool
    is_new_matchup: bool
    distinct_used: int
    limit: int
    warn: bool


async def distinct_matchup_count(
    connection: AsyncConnection, user_id: UUID, cycle_started_at: datetime
) -> int:
    subquery = (
        select(UsageLog.home_team, UsageLog.away_team, UsageLog.neutral)
        .where(UsageLog.user_id == user_id, UsageLog.simulated_at >= cycle_started_at)
        .distinct()
        .subquery()
    )
    result = await connection.execute(select(func.count()).select_from(subquery))
    return result.scalar_one()


async def _matchup_already_used(
    connection: AsyncConnection,
    user_id: UUID,
    home_team: str,
    away_team: str,
    neutral: bool,
    cycle_started_at: datetime,
) -> bool:
    result = await connection.execute(
        select(UsageLog.id).where(
            UsageLog.user_id == user_id,
            UsageLog.home_team == home_team,
            UsageLog.away_team == away_team,
            UsageLog.neutral == neutral,
            UsageLog.simulated_at >= cycle_started_at,
        )
    )
    return result.first() is not None


async def check_quota(
    connection: AsyncConnection,
    user_id: UUID,
    home_team: str,
    away_team: str,
    neutral: bool,
    cycle_started_at: datetime,
    quota_limit: int,
) -> QuotaCheck:
    is_repeat = await _matchup_already_used(
        connection, user_id, home_team, away_team, neutral, cycle_started_at
    )
    used = await distinct_matchup_count(connection, user_id, cycle_started_at)
    warning_floor = quota_limit * QUOTA_WARNING_RATIO
    if is_repeat:
        return QuotaCheck(
            allowed=True,
            is_new_matchup=False,
            distinct_used=used,
            limit=quota_limit,
            warn=used >= warning_floor,
        )
    allowed = used < quota_limit
    return QuotaCheck(
        allowed=allowed,
        is_new_matchup=True,
        distinct_used=used,
        limit=quota_limit,
        warn=allowed and (used + 1) >= warning_floor,
    )


async def record_usage(
    connection: AsyncConnection,
    user_id: UUID,
    home_team: str,
    away_team: str,
    neutral: bool,
    prediction_id: int | None = None,
) -> None:
    await connection.execute(
        UsageLog.__table__.insert().values(
            id=uuid.uuid4(),
            user_id=user_id,
            home_team=home_team,
            away_team=away_team,
            neutral=neutral,
            simulated_at=datetime.now(UTC),
            prediction_id=prediction_id,
        )
    )
