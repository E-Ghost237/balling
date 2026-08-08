from datetime import UTC, datetime, timedelta
from uuid import uuid4

from webapp.db import get_engine
from webapp.models import User, VerificationCode
from webapp.otp import (
    PURPOSE_EMAIL_VERIFY,
    create_verification_code,
    seconds_until_resend_allowed,
    verify_code,
)
from webapp.security import hash_password


async def _create_user() -> object:
    user_id = uuid4()
    async with get_engine().begin() as connection:
        await connection.execute(
            User.__table__.insert().values(
                id=user_id, login="otp-user@example.com", password_hash=hash_password("hunter22"),
                is_admin=False, email_verified=False, created_at=datetime.now(UTC),
            )
        )
    return user_id


async def test_correct_code_verifies_successfully() -> None:
    user_id = await _create_user()
    async with get_engine().begin() as connection:
        code = await create_verification_code(connection, user_id, PURPOSE_EMAIL_VERIFY)
    async with get_engine().begin() as connection:
        assert await verify_code(connection, user_id, PURPOSE_EMAIL_VERIFY, code) is True


async def test_wrong_code_is_rejected() -> None:
    user_id = await _create_user()
    async with get_engine().begin() as connection:
        await create_verification_code(connection, user_id, PURPOSE_EMAIL_VERIFY)
    async with get_engine().begin() as connection:
        assert await verify_code(connection, user_id, PURPOSE_EMAIL_VERIFY, "000000") is False


async def test_code_is_single_use() -> None:
    user_id = await _create_user()
    async with get_engine().begin() as connection:
        code = await create_verification_code(connection, user_id, PURPOSE_EMAIL_VERIFY)
    async with get_engine().begin() as connection:
        assert await verify_code(connection, user_id, PURPOSE_EMAIL_VERIFY, code) is True
    async with get_engine().begin() as connection:
        assert await verify_code(connection, user_id, PURPOSE_EMAIL_VERIFY, code) is False


async def test_expired_code_is_rejected() -> None:
    user_id = await _create_user()
    async with get_engine().begin() as connection:
        code = await create_verification_code(connection, user_id, PURPOSE_EMAIL_VERIFY)
        await connection.execute(
            VerificationCode.__table__.update()
            .where(VerificationCode.user_id == user_id)
            .values(expires_at=datetime.now(UTC) - timedelta(minutes=1))
        )
    async with get_engine().begin() as connection:
        assert await verify_code(connection, user_id, PURPOSE_EMAIL_VERIFY, code) is False


async def test_resend_cooldown_blocks_immediate_resend() -> None:
    user_id = await _create_user()
    async with get_engine().begin() as connection:
        await create_verification_code(connection, user_id, PURPOSE_EMAIL_VERIFY)
    async with get_engine().begin() as connection:
        wait = await seconds_until_resend_allowed(connection, user_id, PURPOSE_EMAIL_VERIFY)
    assert wait > 0
