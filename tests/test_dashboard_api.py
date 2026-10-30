"""The dashboard's read-only API: bookings, calls, caller messages and tool timing.

These run against real Postgres. The London day bounds, the ordering and the percentiles are all
worked out from timestamps in the database, and a fake would not show a wrong answer.
"""

from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from helpers import insert_booking
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from maitre_d.models import Booking, Call, CallerMessage, ToolInvocation

LONDON = ZoneInfo("Europe/London")
T0 = datetime(2026, 10, 23, 10, 0, tzinfo=UTC)


def london(year: int, month: int, day: int, hour: int = 0, minute: int = 0) -> datetime:
    """A London wall-clock time. zoneinfo picks the offset, so these tests don't hand-write one."""
    return datetime(year, month, day, hour, minute, tzinfo=LONDON)


@pytest.fixture
def now() -> datetime:
    """23:30 UTC on 14 July, which is 00:30 on 15 July in London.

    The bookings tests that leave out `date` must see the 15th, so this sits on the far side of
    midnight from UTC.
    """
    return datetime(2026, 7, 14, 23, 30, tzinfo=UTC)


def _call(
    conversation_id: str,
    *,
    started_at: datetime | None,
    received_at: datetime = T0,
) -> Call:
    return Call(
        conversation_id=conversation_id,
        agent_id="agent_saffron",
        started_at=started_at,
        duration_secs=60 if started_at else None,
        call_successful="success",
        summary=f"Summary for {conversation_id}",
        transcript=[],
        analysis=None,
        call_metadata=None,
        received_at=received_at,
    )


def _invocation(
    conversation_id: str,
    tool: str,
    *,
    http_status: int,
    server_ms: float,
    created_at: datetime = T0,
    request: dict[str, Any] | None = None,
    response: dict[str, Any] | None = None,
) -> ToolInvocation:
    return ToolInvocation(
        conversation_id=conversation_id,
        tool=tool,
        ok=http_status == 200,
        error=None if http_status == 200 else "invalid_request",
        http_status=http_status,
        server_ms=server_ms,
        request=request or {},
        response=response,
        created_at=created_at,
    )


# Bookings


async def test_bookings_default_to_the_london_day_even_when_utc_is_still_the_day_before(
    client: AsyncClient, session: AsyncSession
) -> None:
    """Just after midnight in London, the default list is already the new day.

    00:30 on the 15th is 23:30 UTC on the 14th. Working the day out in UTC would show the 14th and
    hide the first booking of the 15th.
    """
    await insert_booking(
        session,
        table_id=1,
        starts_at=london(2026, 7, 15, 0, 30),
        ends_at=london(2026, 7, 15, 2, 0),
        name="Just After Midnight",
    )
    await insert_booking(
        session,
        table_id=2,
        starts_at=london(2026, 7, 15, 20, 0),
        ends_at=london(2026, 7, 15, 21, 30),
        name="Evening Of The Fifteenth",
    )
    await insert_booking(
        session,
        table_id=3,
        starts_at=london(2026, 7, 14, 21, 0),
        ends_at=london(2026, 7, 14, 22, 30),
        name="Yesterday",
    )

    response = await client.get("/api/bookings")

    assert response.status_code == 200
    body: list[dict[str, Any]] = response.json()
    assert [b["guest_name"] for b in body] == ["Just After Midnight", "Evening Of The Fifteenth"]


async def test_a_clock_change_day_lists_all_25_hours_of_bookings(
    client: AsyncClient, session: AsyncSession
) -> None:
    """On Sunday 25 October the clocks go back at 02:00, so the London day is 25 hours long.

    It runs from 00:00 BST (23:00 UTC on the 24th) to 00:00 GMT on the 26th. A fixed 24-hour window
    would close at 23:00 UTC on the 25th and drop the 23:30 booking, which is 23:30 GMT.
    """
    await insert_booking(
        session,
        table_id=1,
        starts_at=london(2026, 10, 25, 0, 30),
        ends_at=london(2026, 10, 25, 2, 0),
        name="Early On The Long Day",
    )
    await insert_booking(
        session,
        table_id=2,
        starts_at=london(2026, 10, 25, 23, 30),
        ends_at=london(2026, 10, 26, 1, 0),
        name="Late On The Long Day",
    )
    await insert_booking(
        session,
        table_id=3,
        starts_at=london(2026, 10, 26, 0, 30),
        ends_at=london(2026, 10, 26, 2, 0),
        name="Monday",
    )

    response = await client.get("/api/bookings", params={"date": "2026-10-25"})

    body: list[dict[str, Any]] = response.json()
    assert [b["guest_name"] for b in body] == [
        "Early On The Long Day",
        "Late On The Long Day",
    ]


async def test_bookings_come_back_in_start_order_whatever_order_they_were_written(
    client: AsyncClient, session: AsyncSession
) -> None:
    await insert_booking(
        session,
        table_id=1,
        starts_at=london(2026, 10, 23, 20, 0),
        ends_at=london(2026, 10, 23, 21, 30),
        name="Eight",
    )
    await insert_booking(
        session,
        table_id=2,
        starts_at=london(2026, 10, 23, 12, 30),
        ends_at=london(2026, 10, 23, 14, 0),
        name="Half Twelve",
    )
    await insert_booking(
        session,
        table_id=3,
        starts_at=london(2026, 10, 23, 18, 0),
        ends_at=london(2026, 10, 23, 19, 30),
        name="Six",
    )

    response = await client.get("/api/bookings", params={"date": "2026-10-23"})

    body: list[dict[str, Any]] = response.json()
    assert [b["guest_name"] for b in body] == ["Half Twelve", "Six", "Eight"]


async def test_a_cancelled_booking_is_listed_and_marked_cancelled(
    client: AsyncClient, session: AsyncSession
) -> None:
    """Staff need to see who cancelled, so cancelled bookings stay on the page, marked as such."""
    await insert_booking(
        session,
        table_id=1,
        starts_at=london(2026, 10, 23, 19, 0),
        ends_at=london(2026, 10, 23, 20, 30),
        name="Kept",
    )
    await insert_booking(
        session,
        table_id=2,
        starts_at=london(2026, 10, 23, 19, 30),
        ends_at=london(2026, 10, 23, 21, 0),
        name="Cancelled",
        status="cancelled",
    )

    response = await client.get("/api/bookings", params={"date": "2026-10-23"})

    body: list[dict[str, Any]] = response.json()
    assert [(b["guest_name"], b["status"]) for b in body] == [
        ("Kept", "confirmed"),
        ("Cancelled", "cancelled"),
    ]


async def test_a_booking_row_carries_the_fields_the_dashboard_shows(
    client: AsyncClient, session: AsyncSession
) -> None:
    """Times are London wall-clock for staff and UTC for the machine; the table is its label."""
    session.add(
        Booking(
            reference="K7M2QX",
            table_id=5,
            party_size=4,
            starts_at=london(2026, 10, 23, 20, 0),
            ends_at=london(2026, 10, 23, 21, 30),
            guest_name="Nadia Ahmed",
            guest_phone="+447700900123",
            notes="Anniversary, quiet table please",
            status="confirmed",
            conversation_id="conv_nadia",
        )
    )
    await session.commit()

    response = await client.get("/api/bookings", params={"date": "2026-10-23"})

    assert response.json() == [
        {
            "reference": "K7M2QX",
            "table": "T5",
            "party_size": 4,
            "date": "2026-10-23",
            "time": "20:00",
            "starts_at": "2026-10-23T19:00:00Z",
            "guest_name": "Nadia Ahmed",
            "guest_phone": "+447700900123",
            "notes": "Anniversary, quiet table please",
            "status": "confirmed",
            "conversation_id": "conv_nadia",
        }
    ]


async def test_an_explicit_date_lists_that_london_day_and_no_other(
    client: AsyncClient, session: AsyncSession
) -> None:
    await insert_booking(
        session,
        table_id=1,
        starts_at=london(2026, 10, 22, 19, 0),
        ends_at=london(2026, 10, 22, 20, 30),
        name="The Day Before",
    )
    await insert_booking(
        session,
        table_id=2,
        starts_at=london(2026, 10, 23, 19, 0),
        ends_at=london(2026, 10, 23, 20, 30),
        name="The Day Asked For",
    )
    await insert_booking(
        session,
        table_id=3,
        starts_at=london(2026, 10, 24, 19, 0),
        ends_at=london(2026, 10, 24, 20, 30),
        name="The Day After",
    )

    response = await client.get("/api/bookings", params={"date": "2026-10-23"})

    body: list[dict[str, Any]] = response.json()
    assert [b["guest_name"] for b in body] == ["The Day Asked For"]


@pytest.mark.parametrize("malformed", ["30/10/2026", "2026-02-30", "tomorrow"])
async def test_a_malformed_date_is_refused_with_422(client: AsyncClient, malformed: str) -> None:
    response = await client.get("/api/bookings", params={"date": malformed})

    assert response.status_code == 422


# Calls


async def test_calls_come_back_newest_first_with_unstarted_calls_last(
    client: AsyncClient, session: AsyncSession
) -> None:
    """A call with no start time (a connection that never got going) sorts after every started
    call, and among those, by when it arrived."""
    session.add_all(
        [
            _call("conv_older", started_at=london(2026, 10, 20, 12, 0)),
            _call("conv_newer", started_at=london(2026, 10, 22, 18, 0)),
            _call(
                "conv_unstarted_recent",
                started_at=None,
                received_at=datetime(2026, 10, 23, 9, 0, tzinfo=UTC),
            ),
            _call(
                "conv_unstarted_earlier",
                started_at=None,
                received_at=datetime(2026, 10, 21, 9, 0, tzinfo=UTC),
            ),
        ]
    )
    await session.commit()

    response = await client.get("/api/calls")

    body: list[dict[str, Any]] = response.json()
    assert [c["conversation_id"] for c in body] == [
        "conv_newer",
        "conv_older",
        "conv_unstarted_recent",
        "conv_unstarted_earlier",
    ]


async def test_the_calls_limit_caps_the_list_and_refuses_values_outside_1_to_200(
    client: AsyncClient, session: AsyncSession
) -> None:
    session.add_all(
        [_call(f"conv_{hour}", started_at=T0 + timedelta(hours=hour)) for hour in range(3)]
    )
    await session.commit()

    limited = await client.get("/api/calls", params={"limit": 2})
    body: list[dict[str, Any]] = limited.json()
    assert [c["conversation_id"] for c in body] == ["conv_2", "conv_1"]

    assert (await client.get("/api/calls", params={"limit": 0})).status_code == 422
    assert (await client.get("/api/calls", params={"limit": 201})).status_code == 422


async def test_a_call_returns_its_transcript_analysis_and_metadata_exactly_as_stored(
    client: AsyncClient, session: AsyncSession
) -> None:
    transcript: list[dict[str, Any]] = [
        {"role": "user", "message": "A table for two, please.", "time_in_call_secs": 0.5},
        {
            "role": "agent",
            "message": None,
            "time_in_call_secs": 2,
            "tool_calls": [{"tool_name": "check_availability", "params_as_json": "{}"}],
        },
    ]
    analysis = {"call_successful": "success", "transcript_summary": "Booked a table."}
    metadata = {"call_duration_secs": 62, "start_time_unix_secs": 1792000000}
    session.add(
        Call(
            conversation_id="conv_full",
            agent_id="agent_saffron",
            started_at=T0,
            duration_secs=62,
            call_successful="success",
            summary="Booked a table.",
            transcript=transcript,
            analysis=analysis,
            call_metadata=metadata,
            received_at=T0,
        )
    )
    await session.commit()

    response = await client.get("/api/calls/conv_full")

    assert response.status_code == 200
    body: dict[str, Any] = response.json()
    assert body["transcript"] == transcript
    assert body["analysis"] == analysis
    assert body["metadata"] == metadata
    assert body["summary"] == "Booked a table."


async def test_a_call_lists_its_tool_requests_in_order_without_their_bodies(
    client: AsyncClient, session: AsyncSession
) -> None:
    """The request bodies hold guests' names and numbers, which the call view does not need."""
    session.add(_call("conv_tools", started_at=T0))
    session.add_all(
        [
            _invocation(
                "conv_tools",
                "create_booking",
                http_status=200,
                server_ms=14.0,
                created_at=T0 + timedelta(minutes=2),
                request={"name": "Secret Guest", "phone": "+447700900999"},
                response={"ok": True, "say": "Booked."},
            ),
            _invocation(
                "conv_tools",
                "check_availability",
                http_status=200,
                server_ms=9.5,
                created_at=T0 + timedelta(minutes=1),
                request={"date": "2026-10-23"},
                response={"ok": True, "available": True},
            ),
            _invocation(
                "conv_other",
                "find_booking",
                http_status=200,
                server_ms=5.0,
                created_at=T0 + timedelta(minutes=1, seconds=30),
            ),
        ]
    )
    await session.commit()

    response = await client.get("/api/calls/conv_tools")

    body: dict[str, Any] = response.json()
    invocations: list[dict[str, Any]] = body["tool_invocations"]
    assert [t["tool"] for t in invocations] == ["check_availability", "create_booking"]
    assert all(
        set(t) == {"tool", "ok", "error", "http_status", "server_ms", "created_at"}
        for t in invocations
    )
    assert "Secret Guest" not in response.text
    assert "+447700900999" not in response.text


async def test_an_unknown_call_is_a_404_with_a_plain_detail(client: AsyncClient) -> None:
    response = await client.get("/api/calls/conv_nobody")

    assert response.status_code == 404
    assert response.json() == {"detail": "Call not found"}


# Caller messages


async def test_caller_messages_come_back_newest_first_with_their_handled_state(
    client: AsyncClient, session: AsyncSession
) -> None:
    session.add_all(
        [
            CallerMessage(
                conversation_id="conv_a",
                caller_name="Ahmed",
                caller_phone="+447700900111",
                message="Allergy: nuts",
                created_at=T0,
                handled_at=None,
            ),
            CallerMessage(
                conversation_id="conv_b",
                caller_name="Brown",
                caller_phone="+447700900222",
                message="Birthday on Friday",
                created_at=T0 + timedelta(hours=1),
                handled_at=T0 + timedelta(hours=2),
            ),
            CallerMessage(
                conversation_id="conv_c",
                caller_name="Chen",
                caller_phone="+447700900333",
                message="Older message",
                created_at=T0 - timedelta(hours=1),
                handled_at=None,
            ),
        ]
    )
    await session.commit()

    response = await client.get("/api/messages")

    body: list[dict[str, Any]] = response.json()
    assert [m["message"] for m in body] == ["Birthday on Friday", "Allergy: nuts", "Older message"]
    assert set(body[0]) == {
        "id",
        "conversation_id",
        "caller_name",
        "caller_phone",
        "message",
        "created_at",
        "handled_at",
    }
    assert body[0]["handled_at"] is not None
    assert body[1]["handled_at"] is None


# Tool latency


async def test_tool_latency_percentiles_use_only_requests_that_succeeded(
    client: AsyncClient, session: AsyncSession
) -> None:
    """Ten, twenty, thirty and forty milliseconds give p50 25.0 and p95 38.5 (linear interpolation).

    The 401 and 500 rows, each recorded as 0 ms, would pull both numbers down if they counted. A
    tool that has only failed requests has no successful sample, so it does not appear at all.
    """
    session.add_all(
        [
            _invocation("c", "check_availability", http_status=200, server_ms=ms)
            for ms in (10.0, 20.0, 30.0, 40.0)
        ]
        + [
            _invocation("c", "check_availability", http_status=401, server_ms=0.0),
            _invocation("c", "check_availability", http_status=500, server_ms=0.0),
            _invocation("c", "create_booking", http_status=200, server_ms=50.0),
            _invocation("c", "find_booking", http_status=401, server_ms=0.0),
        ]
    )
    await session.commit()

    response = await client.get("/api/metrics/tool-latency")

    assert response.json() == [
        {"tool": "check_availability", "count": 4, "p50_ms": 25.0, "p95_ms": 38.5},
        {"tool": "create_booking", "count": 1, "p50_ms": 50.0, "p95_ms": 50.0},
    ]
