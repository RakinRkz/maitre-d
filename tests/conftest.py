"""Shared fixtures.

Booking tests run against real Postgres, because the guarantees under test live in the database:
the exclusion constraint that stops double-booking and the unique key that stops duplicates.
SQLite or mocks would test a different system.

The test database is rebuilt once per run from the real Alembic migrations, so the migrations
are under test too. Each test that touches the database starts from empty bookings.
"""

import asyncio
from collections.abc import AsyncIterator, Callable, Iterator
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest
from alembic import command
from alembic.config import Config
from fastapi import FastAPI
from helpers import NOW, TOOL_SECRET
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr
from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from maitre_d import bookings
from maitre_d.config import Settings
from maitre_d.db import sync_dining_tables
from maitre_d.main import create_app
from maitre_d.routes.tools import get_now

ROOT = Path(__file__).resolve().parent.parent
# Read through `Settings` so a `TEST_DATABASE_URL` in `.env` is honoured; `os.environ` alone
# ignored it, because pydantic-settings loads `.env` without exporting it.
TEST_DATABASE_URL = Settings().test_database_url


async def _reset_schema(url: str) -> None:
    engine = create_async_engine(url)
    async with engine.begin() as connection:
        await connection.execute(text("DROP SCHEMA public CASCADE"))
        await connection.execute(text("CREATE SCHEMA public"))
    await engine.dispose()


@pytest.fixture(scope="session")
def database_url() -> str:
    # Synchronous on purpose: Alembic's env.py starts its own event loop, which it can't do
    # from inside the one the async tests run on.
    asyncio.run(_reset_schema(TEST_DATABASE_URL))
    # No config file name, so env.py skips fileConfig(), which would otherwise disable the
    # application's loggers for the rest of the run.
    config = Config()
    config.set_main_option("script_location", str(ROOT / "migrations"))
    config.set_main_option("sqlalchemy.url", TEST_DATABASE_URL)
    command.upgrade(config, "head")
    return TEST_DATABASE_URL


@pytest.fixture(scope="session")
async def engine(database_url: str) -> AsyncIterator[AsyncEngine]:
    engine = create_async_engine(database_url)
    async with engine.begin() as connection:
        await sync_dining_tables(connection)
    yield engine
    await engine.dispose()


@pytest.fixture
async def clean_db(engine: AsyncEngine) -> None:
    async with engine.begin() as connection:
        await connection.execute(
            text("TRUNCATE bookings, tool_invocations, caller_messages RESTART IDENTITY")
        )


@pytest.fixture
async def session(engine: AsyncEngine, clean_db: None) -> AsyncIterator[AsyncSession]:
    async with async_sessionmaker(engine, expire_on_commit=False)() as session:
        yield session


@pytest.fixture
def now() -> datetime:
    """The clock the app sees. Override in a test module or test to move it."""
    return NOW


@pytest.fixture
async def app(database_url: str, clean_db: None, now: datetime) -> AsyncIterator[FastAPI]:
    app = create_app(Settings(database_url=database_url, tool_bearer_secret=SecretStr(TOOL_SECRET)))
    app.dependency_overrides[get_now] = lambda: now
    # httpx's ASGI transport doesn't run lifespan events, so run them here.
    async with app.router.lifespan_context(app):
        yield app


@pytest.fixture
async def client(app: FastAPI) -> AsyncIterator[AsyncClient]:
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        headers={"Authorization": f"Bearer {TOOL_SECRET}"},
    ) as client:
        yield client


@pytest.fixture
def reads_before_writes(monkeypatch: pytest.MonkeyPatch) -> Iterator[Callable[[int], None]]:
    """Force the worst interleaving: the next `n` requests all read availability, then all write.

    Without this, concurrent requests on one event loop may happen to run one after another and
    a race test would pass without a race. With it, every request has seen every table free
    before any of them inserts.
    """

    def install(n: int) -> None:
        barrier = asyncio.Barrier(n)
        original = bookings.load_occupied
        arrivals = 0

        async def load_then_wait(*args: Any, **kwargs: Any) -> list[bookings.Occupied]:
            nonlocal arrivals
            result = await original(*args, **kwargs)
            arrivals += 1
            if arrivals <= n:
                await asyncio.wait_for(barrier.wait(), timeout=10)
            return result

        monkeypatch.setattr(bookings, "load_occupied", load_then_wait)

    yield install
