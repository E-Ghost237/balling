import os
import shutil
import tempfile
from pathlib import Path

os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://postgres:test@localhost:55432/balling_test")

_DATA_DIR = Path(__file__).resolve().parent.parent / "data"
_REAL_FOOTBALL_DB = _DATA_DIR / "football.db"
_REAL_METADATA = _DATA_DIR / "football_model_metadata.json"
_TEST_FOOTBALL_DB = Path(tempfile.gettempdir()) / "balling_test_football.db"
_TEST_METADATA = Path(tempfile.gettempdir()) / "balling_test_football_model_metadata.json"
shutil.copyfile(_REAL_FOOTBALL_DB, _TEST_FOOTBALL_DB)
shutil.copyfile(_REAL_METADATA, _TEST_METADATA)
os.environ["FOOTBALL_DB_PATH"] = str(_TEST_FOOTBALL_DB)

import pytest_asyncio  # noqa: E402

from webapp.db import get_engine, reset_engine  # noqa: E402
from webapp.models import Base  # noqa: E402


@pytest_asyncio.fixture(autouse=True)
async def clean_database():
    # Each test function gets its own event loop under pytest-asyncio's
    # default function scope; drop any engine left over from a prior test
    # before binding a fresh one to this loop (see db.reset_engine).
    await reset_engine()
    engine = get_engine()
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.drop_all)
        await connection.run_sync(Base.metadata.create_all)
    yield
    await reset_engine()
