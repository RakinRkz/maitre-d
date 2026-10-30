"""Async engine and session helpers.

There are no module-level engines: the app builds one in its lifespan and keeps it on
`app.state`, so tests can point an app at their own database.
"""

from collections.abc import AsyncIterator

from fastapi import Request
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import (
    AsyncConnection,
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from maitre_d import restaurant
from maitre_d.models import DiningTable


def make_engine(url: str) -> AsyncEngine:
    return create_async_engine(url, pool_pre_ping=True)


def make_sessionmaker(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    sessionmaker: async_sessionmaker[AsyncSession] = request.app.state.sessionmaker
    async with sessionmaker() as session:
        yield session


async def sync_dining_tables(connection: AsyncConnection) -> None:
    """Make `dining_tables` match `restaurant.TABLES`. Safe to run any number of times.

    The tables are reference data defined in code; this table exists so bookings can hold a
    foreign key to them and the dashboard can show labels.
    """
    rows = [{"id": t.id, "label": t.label, "seats": t.seats} for t in restaurant.TABLES]
    statement = insert(DiningTable).values(rows)
    await connection.execute(
        statement.on_conflict_do_update(
            index_elements=[DiningTable.id],
            set_={"label": statement.excluded.label, "seats": statement.excluded.seats},
        )
    )
