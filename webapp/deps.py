from collections.abc import AsyncIterator
from datetime import UTC, datetime

from fastapi import Cookie, Depends
from sqlalchemy.ext.asyncio import AsyncConnection

from webapp.auth import get_session_user, get_subscription, subscription_is_active
from webapp.config import get_settings
from webapp.db import connection_scope
from webapp.models import Subscription, User


class NotAuthenticated(Exception):
    pass


class NotAuthorized(Exception):
    pass


async def get_db() -> AsyncIterator[AsyncConnection]:
    async with connection_scope() as connection:
        yield connection


async def get_optional_user(
    connection: AsyncConnection = Depends(get_db),
    session_token: str | None = Cookie(None, alias=get_settings().session_cookie_name),
) -> User | None:
    if session_token is None:
        return None
    return await get_session_user(connection, session_token)


async def require_login(user: User | None = Depends(get_optional_user)) -> User:
    if user is None:
        raise NotAuthenticated()
    return user


async def require_admin(user: User = Depends(require_login)) -> User:
    if not user.is_admin:
        raise NotAuthorized()
    return user


async def require_active_subscription(
    user: User = Depends(require_login),
    connection: AsyncConnection = Depends(get_db),
) -> tuple[User, Subscription]:
    subscription = await get_subscription(connection, user.id)
    if not subscription_is_active(subscription, datetime.now(UTC)):
        raise NotAuthorized()
    return user, subscription
