"""Builders shared by the tests: requests as the agent sends them, and rows put straight in."""

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from maitre_d.models import Booking

TOOL_SECRET = "test-tool-secret"

# Tuesday 20 October 2026, 10:00 in London (09:00 UTC, still BST). The clocks go back on
# Sunday 25 October, inside the week most tests book into.
NOW = datetime(2026, 10, 20, 9, 0, tzinfo=UTC)


def slot(
    date: str = "2026-10-23", weekday: str = "Friday", time: str = "20:00", party_size: int = 4
) -> dict[str, Any]:
    """A slot as the agent sends it. Friday 23 October 2026 at 8 pm unless told otherwise."""
    return {"date": date, "weekday": weekday, "time": time, "party_size": party_size}


def booking_request(
    conversation_id: str = "conv_test",
    name: str = "Nadia Ahmed",
    phone: str = "07700 900123",
    **slot_fields: Any,
) -> dict[str, Any]:
    return {"conversation_id": conversation_id, "name": name, "phone": phone, **slot(**slot_fields)}


def change_request(
    reference: str,
    conversation_id: str = "conv_test",
    name: str = "Nadia Ahmed",
    phone: str = "07700 900123",
    **fields: Any,
) -> dict[str, Any]:
    """A change as the agent sends it: the identity, plus whichever fields are changing."""
    return {
        "conversation_id": conversation_id,
        "reference": reference,
        "name": name,
        "phone": phone,
        **fields,
    }


async def insert_booking(
    session: AsyncSession,
    *,
    table_id: int,
    starts_at: datetime,
    ends_at: datetime,
    status: str = "confirmed",
    name: str = "Existing Guest",
    phone: str = "+447700900999",
    party_size: int = 2,
) -> Booking:
    """Put a booking straight into the database, bypassing the booking code."""
    booking = Booking(
        reference=uuid.uuid4().hex[:8].upper(),
        table_id=table_id,
        party_size=party_size,
        starts_at=starts_at,
        ends_at=ends_at,
        guest_name=name,
        guest_phone=phone,
        status=status,
    )
    session.add(booking)
    await session.commit()
    return booking
