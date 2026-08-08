import secrets
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncConnection

from webapp.config import get_settings
from webapp.models import VerificationCode
from webapp.security import hash_token

PURPOSE_EMAIL_VERIFY = "EMAIL_VERIFY"
PURPOSE_PASSWORD_RESET = "PASSWORD_RESET"


def generate_code() -> str:
    return f"{secrets.randbelow(1_000_000):06d}"


async def create_verification_code(
    connection: AsyncConnection, user_id: UUID, purpose: str
) -> str:
    code = generate_code()
    now = datetime.now(UTC)
    ttl = timedelta(minutes=get_settings().verification_code_ttl_minutes)
    await connection.execute(
        VerificationCode.__table__.insert().values(
            id=uuid4(),
            user_id=user_id,
            code_hash=hash_token(code),
            purpose=purpose,
            created_at=now,
            expires_at=now + ttl,
            used_at=None,
        )
    )
    return code


async def verify_code(
    connection: AsyncConnection, user_id: UUID, purpose: str, code: str
) -> bool:
    now = datetime.now(UTC)
    result = await connection.execute(
        select(VerificationCode)
        .where(
            VerificationCode.user_id == user_id,
            VerificationCode.purpose == purpose,
            VerificationCode.code_hash == hash_token(code),
            VerificationCode.used_at.is_(None),
            VerificationCode.expires_at > now,
        )
        .order_by(VerificationCode.created_at.desc())
    )
    row = result.mappings().first()
    if row is None:
        return False
    await connection.execute(
        VerificationCode.__table__.update()
        .where(VerificationCode.id == row["id"])
        .values(used_at=now)
    )
    return True


async def seconds_until_resend_allowed(
    connection: AsyncConnection, user_id: UUID, purpose: str
) -> int:
    result = await connection.execute(
        select(VerificationCode.created_at)
        .where(VerificationCode.user_id == user_id, VerificationCode.purpose == purpose)
        .order_by(VerificationCode.created_at.desc())
    )
    last_created = result.scalars().first()
    if last_created is None:
        return 0
    elapsed = (datetime.now(UTC) - last_created).total_seconds()
    remaining = get_settings().verification_resend_cooldown_seconds - elapsed
    return max(0, round(remaining))
