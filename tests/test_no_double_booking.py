"""No table is ever booked twice, however many callers race for it.

The first tests talk to Postgres directly, with no application code at all, to show that the
guarantee belongs to the database. The race tests then drive the real endpoints with every request
forced to read availability before any of them writes, which is the interleaving a Python-only
check cannot survive, and the last test removes the constraint to show that it is the thing
doing the work.
"""

import asyncio
from collections.abc import Callable
from datetime import UTC, date, datetime
from typing import Any

import asyncpg
import pytest
from helpers import booking_request, insert_booking
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from maitre_d import bookings, restaurant

FRIDAY_8PM = datetime(2026, 10, 23, 19, 0, tzinfo=UTC)
FRIDAY_930PM = datetime(2026, 10, 23, 20, 30, tzinfo=UTC)
TABLES_FOR_FOUR = len(restaurant.tables_for(4))

OVERLAPPING_PAIRS = text(
    """
    SELECT count(*) FROM bookings a JOIN bookings b
      ON a.table_id = b.table_id AND a.id < b.id
     AND tstzrange(a.starts_at, a.ends_at, '[)') && tstzrange(b.starts_at, b.ends_at, '[)')
    WHERE a.status = 'confirmed' AND b.status = 'confirmed'
    """
)


async def test_the_database_refuses_an_overlapping_booking_on_its_own(
    session: AsyncSession,
) -> None:
    await insert_booking(session, table_id=1, starts_at=FRIDAY_8PM, ends_at=FRIDAY_930PM)
    with pytest.raises(IntegrityError) as caught:
        await insert_booking(
            session,
            table_id=1,
            starts_at=FRIDAY_8PM.replace(minute=45),
            ends_at=FRIDAY_930PM.replace(minute=45),
        )
    assert isinstance(caught.value.orig.__cause__, asyncpg.ExclusionViolationError)  # type: ignore[union-attr]
    assert caught.value.orig.__cause__.constraint_name == "bookings_no_overlap"  # type: ignore[union-attr]


async def test_back_to_back_sittings_can_share_a_table(session: AsyncSession) -> None:
    """Ranges are half-open: a sitting ending at 21:30 doesn't clash with one starting then."""
    await insert_booking(session, table_id=1, starts_at=FRIDAY_8PM, ends_at=FRIDAY_930PM)
    await insert_booking(
        session, table_id=1, starts_at=FRIDAY_930PM, ends_at=FRIDAY_930PM.replace(hour=22)
    )


async def test_a_cancelled_booking_does_not_hold_its_table(session: AsyncSession) -> None:
    await insert_booking(
        session, table_id=1, starts_at=FRIDAY_8PM, ends_at=FRIDAY_930PM, status="cancelled"
    )
    await insert_booking(session, table_id=1, starts_at=FRIDAY_8PM, ends_at=FRIDAY_930PM)


async def race(client: AsyncClient, requests: list[dict[str, Any]]) -> list[dict[str, Any]]:
    responses = await asyncio.gather(
        *(client.post("/tools/create_booking", json=body) for body in requests)
    )
    assert all(r.status_code == 200 for r in responses)
    return [r.json() for r in responses]


def strangers(count: int) -> list[dict[str, Any]]:
    """Different callers, each in their own call, all wanting Friday at 8 pm for four."""
    return [
        booking_request(conversation_id=f"conv_{i}", name=f"Guest {i}", phone=f"07700 900{100 + i}")
        for i in range(count)
    ]


async def test_callers_racing_for_the_last_tables_get_one_each_and_no_more(
    client: AsyncClient, engine: AsyncEngine, reads_before_writes: Callable[[int], None]
) -> None:
    callers = TABLES_FOR_FOUR * 2
    reads_before_writes(callers)

    replies = await race(client, strangers(callers))

    booked = [r for r in replies if r["ok"]]
    turned_away = [r for r in replies if not r["ok"]]
    assert len(booked) == TABLES_FOR_FOUR
    assert {r["error"] for r in turned_away} == {"slot_taken"}
    assert all("has just gone" in r["say"] for r in turned_away)
    async with engine.connect() as connection:
        assert await connection.scalar(OVERLAPPING_PAIRS) == 0
        tables = await connection.scalar(text("SELECT count(DISTINCT table_id) FROM bookings"))
        assert tables == TABLES_FOR_FOUR


async def test_two_callers_racing_for_the_very_last_table(
    client: AsyncClient,
    session: AsyncSession,
    engine: AsyncEngine,
    reads_before_writes: Callable[[int], None],
) -> None:
    """The scenario from the plan: everything else is taken and two callers want what's left."""
    last_table, *others = restaurant.tables_for(4)
    for table in others:
        await insert_booking(session, table_id=table.id, starts_at=FRIDAY_8PM, ends_at=FRIDAY_930PM)
    reads_before_writes(2)

    first, second = await race(client, strangers(2))

    assert sorted([first["ok"], second["ok"]]) == [False, True]
    async with engine.connect() as connection:
        on_last = await connection.scalar(
            text("SELECT count(*) FROM bookings WHERE table_id = :t"), {"t": last_table.id}
        )
        assert on_last == 1


async def test_booking_writes_for_the_same_day_queue_behind_each_other(
    client: AsyncClient, session: AsyncSession
) -> None:
    """Why the queue exists: unqueued, simultaneous clashing inserts can deadlock inside
    Postgres's exclusion check, which costs a second per deadlock to resolve. That depends on
    timing inside the server, so this test pins down the queue's behaviour directly instead."""
    await bookings._queue_for_day(session, date(2026, 10, 23))  # hold Friday; don't commit

    friday = asyncio.create_task(client.post("/tools/create_booking", json=booking_request()))
    saturday = await client.post(
        "/tools/create_booking",
        json=booking_request(conversation_id="conv_sat", date="2026-10-24", weekday="Saturday"),
    )
    assert saturday.json()["ok"] is True
    await asyncio.sleep(0.3)
    assert not friday.done()

    await session.commit()
    assert (await asyncio.wait_for(friday, timeout=5)).json()["ok"] is True


async def test_without_the_constraint_the_same_race_double_books(
    client: AsyncClient, engine: AsyncEngine, reads_before_writes: Callable[[int], None]
) -> None:
    """The control: proof that the race is real and that the constraint is what stops it.

    With the exclusion constraint dropped, the Python availability check is all that's left, and
    every caller, having seen every table free, takes the same best-fitting one.
    """
    async with engine.begin() as connection:
        definition = await connection.scalar(
            text(
                "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
                "WHERE conname = 'bookings_no_overlap'"
            )
        )
        await connection.execute(text("ALTER TABLE bookings DROP CONSTRAINT bookings_no_overlap"))
    try:
        reads_before_writes(4)
        replies = await race(client, strangers(4))
        assert all(r["ok"] for r in replies)
        async with engine.connect() as connection:
            assert await connection.scalar(OVERLAPPING_PAIRS) > 0
    finally:
        async with engine.begin() as connection:
            await connection.execute(text("TRUNCATE bookings"))
            await connection.execute(
                text(f"ALTER TABLE bookings ADD CONSTRAINT bookings_no_overlap {definition}")
            )
