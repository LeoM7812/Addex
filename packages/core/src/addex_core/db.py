import os

from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine

DEFAULT_DATABASE_URL = "postgresql+psycopg://addex:addex@localhost:5433/addex"


def database_url() -> str:
    return os.environ.get("ADDEX_DATABASE_URL", DEFAULT_DATABASE_URL)


def make_engine(url: str | None = None) -> AsyncEngine:
    return create_async_engine(url or database_url(), pool_pre_ping=True)


def make_sessionmaker(engine: AsyncEngine) -> async_sessionmaker:
    return async_sessionmaker(engine, expire_on_commit=False)
