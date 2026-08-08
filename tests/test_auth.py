from datetime import UTC, datetime, timedelta
from uuid import uuid4

from webapp.auth import authenticate, create_session, get_session_user, get_subscription
from webapp.db import get_engine
from webapp.models import Subscription, User
from webapp.security import generate_session_token, hash_password


async def _create_user(login: str = "user1", password: str = "hunter22") -> object:
    user_id = uuid4()
    async with get_engine().begin() as connection:
        await connection.execute(
            User.__table__.insert().values(
                id=user_id,
                login=login,
                password_hash=hash_password(password),
                is_admin=False,
                created_at=datetime.now(UTC),
            )
        )
    return user_id


async def test_authenticate_accepts_correct_password() -> None:
    await _create_user()
    async with get_engine().begin() as connection:
        user = await authenticate(connection, "user1", "hunter22")
    assert user is not None
    assert user.login == "user1"


async def test_authenticate_rejects_wrong_password() -> None:
    await _create_user()
    async with get_engine().begin() as connection:
        user = await authenticate(connection, "user1", "wrong-password")
    assert user is None


async def test_authenticate_rejects_unknown_login() -> None:
    async with get_engine().begin() as connection:
        user = await authenticate(connection, "nobody", "whatever")
    assert user is None


async def test_new_login_invalidates_prior_session() -> None:
    user_id = await _create_user()
    token1 = generate_session_token()
    async with get_engine().begin() as connection:
        await create_session(connection, user_id, token1)

    token2 = generate_session_token()
    async with get_engine().begin() as connection:
        await create_session(connection, user_id, token2)

    async with get_engine().begin() as connection:
        assert await get_session_user(connection, token1) is None
        second_session_user = await get_session_user(connection, token2)
    assert second_session_user is not None
    assert second_session_user.id == user_id


async def test_unknown_token_resolves_to_no_user() -> None:
    async with get_engine().begin() as connection:
        assert await get_session_user(connection, "not-a-real-token") is None


async def test_stale_free_plan_cycle_rolls_forward_automatically() -> None:
    """The free plan has no payment to trigger a renewal, so get_subscription
    itself rolls a >=30-day-old cycle forward on read — this is what resets
    a free user's monthly quota without needing a scheduler."""
    user_id = await _create_user("free-user")
    stale_start = datetime.now(UTC) - timedelta(days=31)
    async with get_engine().begin() as connection:
        await connection.execute(
            Subscription.__table__.insert().values(
                id=uuid4(), user_id=user_id, status="ACTIVE", plan="free", quota_limit=20,
                cycle_started_at=stale_start, expires_at=datetime.now(UTC) + timedelta(days=36500),
            )
        )
    async with get_engine().begin() as connection:
        subscription = await get_subscription(connection, user_id)
    assert subscription.cycle_started_at > stale_start

    async with get_engine().begin() as connection:
        result = await connection.execute(
            Subscription.__table__.select().where(Subscription.user_id == user_id)
        )
        row = result.mappings().one()
    assert row["cycle_started_at"] > stale_start


async def test_fresh_free_plan_cycle_is_not_rolled_forward() -> None:
    user_id = await _create_user("fresh-free-user")
    fresh_start = datetime.now(UTC) - timedelta(days=1)
    async with get_engine().begin() as connection:
        await connection.execute(
            Subscription.__table__.insert().values(
                id=uuid4(), user_id=user_id, status="ACTIVE", plan="free", quota_limit=20,
                cycle_started_at=fresh_start, expires_at=datetime.now(UTC) + timedelta(days=36500),
            )
        )
    async with get_engine().begin() as connection:
        subscription = await get_subscription(connection, user_id)
    assert subscription.cycle_started_at == fresh_start
