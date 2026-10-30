"""The booking tools end to end, through HTTP, as the agent calls them.

Friday 23 October 2026 is the usual test evening. "Now" is Tuesday 20 October at 10:00 in London.
"""

from datetime import UTC, datetime
from typing import Any

import pytest
from helpers import booking_request, change_request, insert_booking, slot
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from maitre_d import restaurant

FRIDAY = (2026, 10, 23)


def london_friday(hour: int, minute: int = 0) -> datetime:
    """A UTC instant for a London wall-clock time on Friday 23 October (BST, so minus an hour)."""
    return datetime(*FRIDAY, hour - 1, minute, tzinfo=UTC)


async def call(client: AsyncClient, tool: str, body: dict[str, Any]) -> dict[str, Any]:
    response = await client.post(f"/tools/{tool}", json=body)
    assert response.status_code == 200, response.text
    reply: dict[str, Any] = response.json()
    return reply


def who(
    reference: str = "", name: str = "Nadia Ahmed", phone: str = "07700 900123"
) -> dict[str, Any]:
    identity = {"conversation_id": "conv_test", "name": name, "phone": phone}
    return identity | ({"reference": reference} if reference else {})


async def fill_tables_for_four(
    session: AsyncSession, starts_at: datetime, ends_at: datetime
) -> None:
    for table in restaurant.tables_for(4):
        await insert_booking(session, table_id=table.id, starts_at=starts_at, ends_at=ends_at)


async def table_of(engine: AsyncEngine, reference: str) -> int:
    async with engine.connect() as connection:
        table_id = await connection.scalar(
            text("SELECT table_id FROM bookings WHERE reference = :r"), {"r": reference}
        )
    assert isinstance(table_id, int)
    return table_id


async def test_an_open_slot_is_available(client: AsyncClient) -> None:
    reply = await call(client, "check_availability", {"conversation_id": "c", **slot()})
    assert reply == {
        "ok": True,
        "available": True,
        "slot": {"date": "2026-10-23", "time": "20:00", "spoken": "Friday 23 October at 8 pm"},
        "say": "Yes, we have a table for 4 on Friday 23 October at 8 pm.",
    }


async def test_a_full_slot_offers_the_nearest_free_times_that_evening(
    client: AsyncClient, session: AsyncSession
) -> None:
    """Every table for four is held 20:00-21:30. A 90-minute sitting has to end by 20:00 to fit
    before, and none can start after 21:00, so within 90 minutes the only option is 18:30."""
    await fill_tables_for_four(session, london_friday(20), london_friday(21, 30))
    reply = await call(client, "check_availability", {"conversation_id": "c", **slot()})
    assert reply["available"] is False
    assert [a["time"] for a in reply["alternatives"]] == ["18:30"]
    assert reply["say"] == (
        "Friday 23 October at 8 pm is fully booked for 4. I can offer 6:30 pm that day."
    )


async def test_a_day_with_nothing_nearby_offers_the_same_time_on_later_days(
    client: AsyncClient, session: AsyncSession
) -> None:
    """Friday's dinner is full, so try 8 pm on later days: Saturday, then Sunday (the day the
    clocks go back), skipping Monday, when the restaurant is closed."""
    await fill_tables_for_four(session, london_friday(17, 30), london_friday(22, 30))
    reply = await call(client, "check_availability", {"conversation_id": "c", **slot()})
    assert [a["date"] for a in reply["alternatives"]] == ["2026-10-24", "2026-10-25", "2026-10-27"]
    assert reply["say"] == (
        "Friday 23 October at 8 pm is fully booked for 4. 8 pm is free on Saturday 24 October, "
        "Sunday 25 October or Tuesday 27 October."
    )


async def test_booking_a_slot_that_was_already_full_says_full_not_just_gone(
    client: AsyncClient, session: AsyncSession
) -> None:
    await fill_tables_for_four(session, london_friday(20), london_friday(21, 30))
    reply = await call(client, "create_booking", booking_request())
    assert reply["error"] == "slot_taken"
    assert reply["say"].startswith("Friday 23 October at 8 pm is fully booked for 4.")


async def test_a_party_gets_the_smallest_table_that_fits(
    client: AsyncClient, engine: AsyncEngine
) -> None:
    couple = await call(client, "create_booking", booking_request(party_size=2))
    assert await table_of(engine, couple["booking"]["reference"]) == 1


async def test_a_booking_reads_back_everything_the_caller_needs(client: AsyncClient) -> None:
    reply = await call(client, "create_booking", booking_request())
    reference = reply["booking"]["reference"]
    assert reply["say"] == (
        f"Booked: a table for 4 on Friday 23 October at 8 pm under Nadia Ahmed. "
        f"The reference is {reference}."
    )


@pytest.mark.parametrize(
    ("day", "weekday", "now", "utc_hour"),
    [
        ("2026-10-25", "Sunday", datetime(2026, 10, 20, 9, tzinfo=UTC), 19),
        ("2027-03-28", "Sunday", datetime(2027, 3, 20, 10, tzinfo=UTC), 18),
    ],
    ids=["clocks-went-back", "clocks-went-forward"],
)
async def test_a_booking_on_a_clock_change_day_is_stored_at_the_right_instant(
    client: AsyncClient,
    app: Any,
    engine: AsyncEngine,
    day: str,
    weekday: str,
    now: datetime,
    utc_hour: int,
) -> None:
    from maitre_d.routes.tools import get_now

    app.dependency_overrides[get_now] = lambda: now
    reply = await call(
        client, "create_booking", booking_request(date=day, weekday=weekday, time="19:00")
    )
    async with engine.connect() as connection:
        stored = await connection.scalar(
            text("SELECT starts_at FROM bookings WHERE reference = :r"),
            {"r": reply["booking"]["reference"]},
        )
    assert stored == datetime.fromisoformat(day).replace(hour=utc_hour, tzinfo=UTC)
    assert reply["booking"]["time"] == "19:00"


async def test_find_change_and_cancel_a_booking(client: AsyncClient, engine: AsyncEngine) -> None:
    created = await call(client, "create_booking", booking_request())
    reference = created["booking"]["reference"]

    found = await call(client, "find_booking", who())
    assert [b["reference"] for b in found["bookings"]] == [reference]
    assert found["say"] == "I found one booking: a table for 4 on Friday 23 October at 8 pm."

    changed = await call(client, "change_booking", who(reference) | {"time": "20:30"})
    assert (
        changed["say"] == "Done. The booking is now a table for 4 on Friday 23 October at 8:30 pm."
    )

    cancelled = await call(client, "cancel_booking", who(reference))
    assert cancelled["say"] == "Cancelled: a table for 4 on Friday 23 October at 8:30 pm."
    assert (await call(client, "find_booking", who()))["error"] == "not_found"


async def test_a_change_to_a_new_date_moves_the_booking_and_keeps_the_reference(
    client: AsyncClient,
) -> None:
    created = await call(client, "create_booking", booking_request())
    reference = created["booking"]["reference"]
    changed = await call(
        client, "change_booking", change_request(reference, date="2026-10-24", weekday="Saturday")
    )
    assert changed["ok"] is True
    assert changed["booking"]["reference"] == reference
    assert changed["booking"]["date"] == "2026-10-24"


async def test_a_party_size_change_keeps_the_reference(client: AsyncClient) -> None:
    created = await call(client, "create_booking", booking_request(party_size=2))
    reference = created["booking"]["reference"]
    changed = await call(client, "change_booking", change_request(reference, party_size=4))
    assert changed["ok"] is True
    assert changed["booking"]["reference"] == reference
    assert changed["booking"]["party_size"] == 4


async def test_a_weekday_without_a_date_is_rejected_over_http(client: AsyncClient) -> None:
    created = await call(client, "create_booking", booking_request())
    reply = await call(
        client,
        "change_booking",
        change_request(created["booking"]["reference"], weekday="Saturday"),
    )
    assert reply["ok"] is False
    assert reply["error"] == "invalid_request"
    assert "date is required when weekday is given" in reply["detail"]
    assert reply["say"] == "Sorry, I didn't catch that. Could you say it again?"


async def test_a_change_keeps_the_guest_at_their_table_when_it_can(
    client: AsyncClient, engine: AsyncEngine
) -> None:
    created = await call(client, "create_booking", booking_request())
    reference = created["booking"]["reference"]
    before = await table_of(engine, reference)
    await call(client, "change_booking", who(reference) | {"time": "19:45"})
    assert await table_of(engine, reference) == before


async def test_a_change_to_a_full_slot_offers_alternatives_and_keeps_the_booking(
    client: AsyncClient, session: AsyncSession
) -> None:
    created = await call(client, "create_booking", booking_request(time="18:00"))
    reference = created["booking"]["reference"]
    await fill_tables_for_four(session, london_friday(20), london_friday(21, 30))

    reply = await call(client, "change_booking", who(reference) | {"time": "20:00"})

    assert reply["error"] == "slot_taken"
    assert reply["alternatives"]
    still = await call(client, "find_booking", who())
    assert still["bookings"][0]["time"] == "18:00"


async def test_changing_to_the_same_time_changes_nothing(client: AsyncClient) -> None:
    created = await call(client, "create_booking", booking_request())
    reply = await call(
        client, "change_booking", who(created["booking"]["reference"]) | {"time": "20:00"}
    )
    assert reply["ok"] is True
    assert reply["say"].endswith("Nothing needed changing.")


async def test_cancelling_twice_is_fine(client: AsyncClient) -> None:
    created = await call(client, "create_booking", booking_request())
    reference = created["booking"]["reference"]
    await call(client, "cancel_booking", who(reference))
    again = await call(client, "cancel_booking", who(reference))
    assert again["ok"] is True
    assert again["say"] == "That booking was already cancelled."
    assert again["booking"]["status"] == "cancelled"


async def test_a_cancelled_booking_frees_its_table(
    client: AsyncClient, session: AsyncSession
) -> None:
    await fill_tables_for_four(session, london_friday(20), london_friday(21, 30))
    await session.execute(text("UPDATE bookings SET status = 'cancelled' WHERE table_id = 5"))
    await session.commit()
    reply = await call(client, "check_availability", {"conversation_id": "c", **slot()})
    assert reply["available"] is True


async def test_two_bookings_for_the_same_caller_are_both_found(client: AsyncClient) -> None:
    await call(client, "create_booking", booking_request())
    await call(client, "create_booking", booking_request(date="2026-10-24", weekday="Saturday"))
    reply = await call(client, "find_booking", who())
    assert len(reply["bookings"]) == 2
    assert reply["say"].startswith("I found 2 bookings:")


async def test_a_title_and_surname_find_the_booking(client: AsyncClient) -> None:
    await call(client, "create_booking", booking_request())
    reply = await call(client, "find_booking", who(name="Mrs Ahmed", phone="+447700900123"))
    assert reply["ok"] is True


async def test_a_wrong_name_looks_exactly_like_no_booking(client: AsyncClient) -> None:
    """The right number with the wrong name must not reveal that a booking exists."""
    await call(client, "create_booking", booking_request())
    right_number = await call(client, "find_booking", who(name="Nadia Khan"))
    unused_number = await call(client, "find_booking", who(name="Nadia Khan", phone="07700 900555"))
    assert right_number == unused_number
    assert right_number["error"] == "not_found"


async def test_the_reference_alone_does_not_cancel_a_booking(
    client: AsyncClient, engine: AsyncEngine
) -> None:
    created = await call(client, "create_booking", booking_request())
    reference = created["booking"]["reference"]
    attempt = await call(
        client, "cancel_booking", who(reference, name="Nadia Ahmed", phone="07700 900999")
    )
    assert attempt["error"] == "not_found"
    async with engine.connect() as connection:
        status = await connection.scalar(
            text("SELECT status FROM bookings WHERE reference = :r"), {"r": reference}
        )
    assert status == "confirmed"


async def test_a_message_for_the_team_is_kept_once(
    client: AsyncClient, engine: AsyncEngine
) -> None:
    body = {
        "conversation_id": "conv_test",
        "name": "Sam Okafor",
        "phone": "07700 900321",
        "message": "Booking for twelve on 5 December, please call back.",
    }
    first = await call(client, "take_message", body)
    await call(client, "take_message", body)
    assert first == {
        "ok": True,
        "say": "I've passed the message to the team. Someone will call back on the number given.",
    }
    async with engine.connect() as connection:
        rows = await connection.execute(text("SELECT caller_phone FROM caller_messages"))
        assert [r.caller_phone for r in rows] == ["+447700900321"]


async def test_a_number_that_cannot_be_a_phone_number_is_asked_for_again(
    client: AsyncClient,
) -> None:
    reply = await call(client, "create_booking", booking_request(phone="0770"))
    assert reply["error"] == "invalid_phone"


async def test_a_message_with_an_unreadable_phone_keeps_the_number_as_said(
    client: AsyncClient, engine: AsyncEngine
) -> None:
    reply = await call(
        client,
        "take_message",
        {
            "conversation_id": "conv_test",
            "name": "Sam Okafor",
            "phone": "0770",
            "message": "Please call me back about a large party.",
        },
    )
    assert reply["ok"] is True
    async with engine.connect() as connection:
        stored = await connection.scalar(text("SELECT caller_phone FROM caller_messages"))
    assert stored == "0770"


async def test_change_and_cancel_with_an_unreadable_phone_ask_for_it_again(
    client: AsyncClient, engine: AsyncEngine
) -> None:
    created = await call(client, "create_booking", booking_request())
    reference = created["booking"]["reference"]

    changed = await call(
        client, "change_booking", change_request(reference, phone="0770", time="20:30")
    )
    cancelled = await call(client, "cancel_booking", who(reference) | {"phone": "0770"})
    assert changed["error"] == "invalid_phone"
    assert cancelled["error"] == "invalid_phone"

    async with engine.connect() as connection:
        status = await connection.scalar(
            text("SELECT status FROM bookings WHERE reference = :r"), {"r": reference}
        )
    assert status == "confirmed"
