from datetime import UTC, datetime
from uuid import uuid4

from webapp.db import get_engine
from webapp.models import User
from webapp.quota import check_quota, record_usage
from webapp.security import hash_password


async def _create_user() -> object:
    user_id = uuid4()
    async with get_engine().begin() as connection:
        await connection.execute(
            User.__table__.insert().values(
                id=user_id,
                login="quota-user",
                password_hash=hash_password("hunter22"),
                is_admin=False,
                created_at=datetime.now(UTC),
            )
        )
    return user_id


async def test_first_matchup_is_new_and_allowed() -> None:
    user_id = await _create_user()
    now = datetime.now(UTC)
    async with get_engine().begin() as connection:
        result = await check_quota(connection, user_id, "Arsenal", "Chelsea", False, now, 150)
    assert result.allowed
    assert result.is_new_matchup
    assert result.distinct_used == 0


async def test_repeat_matchup_does_not_consume_quota() -> None:
    user_id = await _create_user()
    now = datetime.now(UTC)
    async with get_engine().begin() as connection:
        await record_usage(connection, user_id, "Arsenal", "Chelsea", False)
    async with get_engine().begin() as connection:
        result = await check_quota(connection, user_id, "Arsenal", "Chelsea", False, now, 150)
    assert result.allowed
    assert not result.is_new_matchup
    assert result.distinct_used == 1


async def test_swapped_home_away_counts_as_distinct() -> None:
    user_id = await _create_user()
    now = datetime.now(UTC)
    async with get_engine().begin() as connection:
        await record_usage(connection, user_id, "Arsenal", "Chelsea", False)
    async with get_engine().begin() as connection:
        result = await check_quota(connection, user_id, "Chelsea", "Arsenal", False, now, 150)
    assert result.is_new_matchup


async def test_quota_blocks_once_limit_reached() -> None:
    user_id = await _create_user()
    now = datetime.now(UTC)
    async with get_engine().begin() as connection:
        await record_usage(connection, user_id, "A", "B", False)
        await record_usage(connection, user_id, "C", "D", False)
    async with get_engine().begin() as connection:
        result = await check_quota(connection, user_id, "E", "F", False, now, 2)
    assert not result.allowed
    assert result.is_new_matchup


async def test_warning_fires_near_limit() -> None:
    user_id = await _create_user()
    now = datetime.now(UTC)
    async with get_engine().begin() as connection:
        await record_usage(connection, user_id, "A", "B", False)
    async with get_engine().begin() as connection:
        # limit=2: after this new matchup, used_after=2 >= 85% of 2 (1.7) -> warn.
        result = await check_quota(connection, user_id, "C", "D", False, now, 2)
    assert result.allowed
    assert result.warn


async def test_warning_scales_with_plan_quota() -> None:
    """A yearly plan's 3,000-matchup limit shouldn't warn at the same
    absolute count that would trip a monthly plan's 150-matchup limit —
    the warning floor is proportional (85%) to each subscription's own
    quota_limit, not a fixed number."""
    user_id = await _create_user()
    now = datetime.now(UTC)
    async with get_engine().begin() as connection:
        for i in range(100):
            await record_usage(connection, user_id, f"Team{i}A", f"Team{i}B", False)
    async with get_engine().begin() as connection:
        result = await check_quota(connection, user_id, "New1", "New2", False, now, 3000)
    assert result.allowed
    assert not result.warn
