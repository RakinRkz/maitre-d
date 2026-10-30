"""`dining_tables` is synced from the restaurant facts, and syncing it again changes nothing.

The table is reference data, so the only safe behaviour is an upsert: no duplicate rows and no
drift from `restaurant.TABLES`.
"""

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from maitre_d import restaurant
from maitre_d.db import sync_dining_tables


async def test_syncing_the_dining_tables_twice_leaves_exactly_one_row_per_table(
    engine: AsyncEngine,
) -> None:
    """The fixture already synced once; two more calls must not duplicate or change any row."""
    async with engine.begin() as connection:
        await sync_dining_tables(connection)
        await sync_dining_tables(connection)

    async with engine.connect() as connection:
        rows = (
            await connection.execute(text("SELECT id, label, seats FROM dining_tables ORDER BY id"))
        ).all()

    assert len(rows) == len(restaurant.TABLES)
    assert {row.id: (row.label, row.seats) for row in rows} == {
        table.id: (table.label, table.seats) for table in restaurant.TABLES
    }
