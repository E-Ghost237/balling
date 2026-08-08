"""Seeds (or updates the password of) the creator's superadmin account from
ADMIN_LOGIN/ADMIN_PASSWORD env vars. Safe to run on every deploy — it's an
upsert, not a one-time script.

Usage: python -m webapp.bootstrap_admin
"""

import asyncio
from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import select, update

from webapp.config import get_settings
from webapp.db import connection_scope, get_engine
from webapp.models import Base, User
from webapp.security import hash_password


async def main() -> None:
    settings = get_settings()
    if not settings.admin_login or not settings.admin_password:
        raise SystemExit("ADMIN_LOGIN and ADMIN_PASSWORD must both be set")

    async with get_engine().begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    async with connection_scope() as connection:
        existing = await connection.execute(
            select(User.id).where(User.login == settings.admin_login)
        )
        user_id = existing.scalar_one_or_none()
        password_hash = hash_password(settings.admin_password)
        if user_id is None:
            await connection.execute(
                User.__table__.insert().values(
                    id=uuid4(),
                    login=settings.admin_login,
                    password_hash=password_hash,
                    is_admin=True,
                    email_verified=True,
                    created_at=datetime.now(UTC),
                )
            )
            print(f"Created superadmin account: {settings.admin_login}")
        else:
            await connection.execute(
                update(User)
                .where(User.id == user_id)
                .values(password_hash=password_hash, is_admin=True)
            )
            print(f"Updated superadmin password: {settings.admin_login}")


if __name__ == "__main__":
    asyncio.run(main())
