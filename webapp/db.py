from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress

from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, create_async_engine

from webapp.config import get_settings

_engine: AsyncEngine | None = None


def get_engine() -> AsyncEngine:
    global _engine
    if _engine is None:
        _engine = create_async_engine(get_settings().database_url, pool_pre_ping=True)
    return _engine


async def reset_engine() -> None:
    """Drop the cached engine so the next get_engine() call binds a fresh
    one to whichever event loop is current. A pooled asyncpg engine is not
    safe to reuse across event loops — needed because pytest-asyncio (and
    Celery's per-task asyncio.run()) each create their own loop, while this
    module's cached engine would otherwise outlive any single one of them."""
    global _engine
    engine = _engine
    _engine = None
    if engine is not None:
        with suppress(Exception):
            await engine.dispose()


@asynccontextmanager
async def connection_scope() -> AsyncIterator[AsyncConnection]:
    engine = get_engine()
    async with engine.begin() as connection:
        yield connection
