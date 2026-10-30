"""One request, one booking, however many times it arrives.

A booking request arrives twice in two ways: the model calls the tool again with the same
arguments, or the platform retries after a timeout while the first attempt is still running. Both
carry the same conversation ID, which the platform fills in and the model cannot change.
"""

import asyncio
from collections.abc import Callable
from typing import Any

from helpers import booking_request, change_request
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine


async def create(client: AsyncClient, body: dict[str, Any]) -> dict[str, Any]:
    response = await client.post("/tools/create_booking", json=body)
    assert response.status_code == 200
    reply: dict[str, Any] = response.json()
    return reply


async def booking_count(engine: AsyncEngine) -> int:
    async with engine.connect() as connection:
        count = await connection.scalar(text("SELECT count(*) FROM bookings"))
    assert isinstance(count, int)
    return count


async def test_a_repeated_request_gets_the_same_booking_and_the_same_reply(
    client: AsyncClient, engine: AsyncEngine
) -> None:
    first = await create(client, booking_request())
    second = await create(client, booking_request())
    assert first["ok"] is True
    assert second == first
    assert await booking_count(engine) == 1


async def test_a_retry_racing_the_original_still_makes_one_booking(
    client: AsyncClient, engine: AsyncEngine, reads_before_writes: Callable[[int], None]
) -> None:
    """Both attempts look for the key before either has written it; the unique key decides."""
    reads_before_writes(2)
    first, second = await asyncio.gather(
        create(client, booking_request()), create(client, booking_request())
    )
    assert first["ok"] and second["ok"]
    assert first["booking"]["reference"] == second["booking"]["reference"]
    assert await booking_count(engine) == 1


async def test_a_retry_that_rewords_the_notes_is_still_the_same_booking(
    client: AsyncClient, engine: AsyncEngine
) -> None:
    first = await create(client, booking_request() | {"notes": "window seat"})
    second = await create(client, booking_request() | {"notes": "a window seat, please"})
    assert second["booking"]["reference"] == first["booking"]["reference"]
    assert await booking_count(engine) == 1


async def test_the_same_details_spoken_differently_are_the_same_booking(
    client: AsyncClient, engine: AsyncEngine
) -> None:
    first = await create(client, booking_request(name="Nadia Ahmed", phone="07700 900123"))
    second = await create(
        client, booking_request(name="nadia  ahmed", phone="+44 7700 900123", time="8pm")
    )
    assert second["booking"]["reference"] == first["booking"]["reference"]
    assert await booking_count(engine) == 1


async def test_a_correction_in_the_same_call_does_not_become_a_second_booking(
    client: AsyncClient, engine: AsyncEngine
) -> None:
    """ "Actually, make it half seven" should be a change, not another table."""
    await create(client, booking_request(time="20:00"))
    corrected = await create(client, booking_request(time="19:30"))
    assert corrected["ok"] is False
    assert corrected["error"] == "already_booked_this_call"
    assert corrected["say"] == (
        "I've already booked you a table for 4 on Friday 23 October at 8 pm. "
        "Would you like me to change that booking instead of making a second one?"
    )
    assert await booking_count(engine) == 1


async def test_two_different_days_in_one_call_are_two_bookings(
    client: AsyncClient, engine: AsyncEngine
) -> None:
    friday = await create(client, booking_request())
    saturday = await create(client, booking_request(date="2026-10-24", weekday="Saturday"))
    assert friday["ok"] and saturday["ok"]
    assert await booking_count(engine) == 2


async def test_a_key_kept_after_a_change_does_not_report_the_old_slot_as_booked(
    client: AsyncClient, engine: AsyncEngine
) -> None:
    """The key survives a move, so a create with it shows the current booking, not Friday."""
    friday = await create(client, booking_request())
    reference = friday["booking"]["reference"]
    moved = await client.post(
        "/tools/change_booking",
        json=change_request(reference, date="2026-10-24", weekday="Saturday"),
    )
    assert moved.json()["ok"] is True

    again = await create(client, booking_request())
    assert again["ok"] is False
    assert again["error"] == "already_booked_this_call"
    assert again["booking"]["date"] == "2026-10-24"
    assert await booking_count(engine) == 1


async def test_after_cancelling_the_same_slot_can_be_booked_again_in_the_same_call(
    client: AsyncClient, engine: AsyncEngine
) -> None:
    first = await create(client, booking_request())
    cancel = await client.post(
        "/tools/cancel_booking",
        json={
            "conversation_id": "conv_test",
            "reference": first["booking"]["reference"],
            "name": "Nadia Ahmed",
            "phone": "07700 900123",
        },
    )
    assert cancel.json()["ok"] is True
    again = await create(client, booking_request())
    assert again["ok"] is True
    assert again["booking"]["reference"] != first["booking"]["reference"]
    assert await booking_count(engine) == 2
