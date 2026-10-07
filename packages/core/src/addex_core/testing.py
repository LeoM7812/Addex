"""Pytest fixtures shared by the Addex packages: a throwaway Postgres session per test.

Import `db` (and `_test_database`) into a conftest.py to use them.
"""

import asyncio
import os
import sys
from collections.abc import Awaitable, Callable

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from addex_core.models import Base

TEST_DATABASE_URL = os.environ.get(
    "ADDEX_TEST_DATABASE_URL", "postgresql+psycopg://addex:addex@localhost:5433/addex_test"
)
LOOP_FACTORY = asyncio.SelectorEventLoop if sys.platform == "win32" else None


def run(coro: Awaitable):
    return asyncio.run(coro, loop_factory=LOOP_FACTORY)


@pytest.fixture(scope="session")
def _test_database() -> str:
    url = make_url(TEST_DATABASE_URL)
    admin = create_engine(url.set(database="postgres"), isolation_level="AUTOCOMMIT")
    try:
        with admin.connect() as conn:
            exists = conn.scalar(
                text("SELECT 1 FROM pg_database WHERE datname = :n"), {"n": url.database}
            )
            if not exists:
                conn.execute(text(f'CREATE DATABASE "{url.database}"'))
    except OperationalError as e:
        pytest.skip(f"test database unavailable: {e.orig}")
    finally:
        admin.dispose()

    engine = create_engine(url)
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    engine.dispose()
    return TEST_DATABASE_URL


DbRunner = Callable[[Callable[[AsyncSession], Awaitable]], object]


@pytest.fixture
def db(_test_database: str) -> DbRunner:
    """Runs `fn(session)` inside a transaction that is rolled back afterwards."""

    def runner(fn):
        async def main():
            engine = create_async_engine(_test_database, poolclass=NullPool)
            try:
                async with engine.connect() as conn:
                    trans = await conn.begin()
                    session = AsyncSession(
                        bind=conn,
                        join_transaction_mode="create_savepoint",
                        expire_on_commit=False,
                    )
                    try:
                        return await fn(session)
                    finally:
                        await session.close()
                        await trans.rollback()
            finally:
                await engine.dispose()

        return run(main())

    return runner
