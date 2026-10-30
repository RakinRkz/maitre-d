"""Read-only endpoints behind the staff dashboard: bookings, calls, caller messages and tool timing.

This is local-only in this design. The routes take no credentials and show guests' names and phone
numbers, so they must not be reachable from the internet. The tunnel that lets ElevenLabs reach
the tools would otherwise expose them, so `require_local_request` refuses anything that arrived
through a proxy or from another host. A deployed version would sit behind a password (plan §9).
The tool bearer token is not the answer: it is a secret held by ElevenLabs, and a browser cannot
keep it.
"""

from datetime import UTC, date, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from maitre_d import timeslots
from maitre_d.dashboard_schemas import (
    BookingRow,
    CallDetail,
    CallRow,
    MessageRow,
    ToolInvocationRow,
    ToolLatencyRow,
)
from maitre_d.db import get_session
from maitre_d.models import Booking, Call, CallerMessage, DiningTable, ToolInvocation
from maitre_d.restaurant import TIMEZONE
from maitre_d.routes.tools import get_now
from maitre_d.security import require_local_request

router = APIRouter(prefix="/api", tags=["dashboard"], dependencies=[Depends(require_local_request)])

Session = Annotated[AsyncSession, Depends(get_session)]
Now = Annotated[datetime, Depends(get_now)]
Limit = Annotated[int, Query(ge=1, le=200)]


@router.get("/bookings", response_model=list[BookingRow])
async def list_bookings(
    session: Session,
    now: Now,
    day: Annotated[date | None, Query(alias="date")] = None,
) -> list[BookingRow]:
    """Bookings starting on one London calendar day, cancelled ones included and marked as such.

    The default day is today in London, not UTC: at 00:30 BST it is still yesterday in UTC, and
    the restaurant's day is the one staff mean by "today".
    """
    london_day = day if day is not None else timeslots.today_in_london(now)
    # Local midnights, not a fixed 24 hours: on a clock-change day the day is 23 or 25 hours long.
    start, end = timeslots.day_bounds(london_day)
    result = await session.execute(
        select(Booking, DiningTable.label)
        .join(DiningTable, DiningTable.id == Booking.table_id)
        .where(Booking.starts_at >= start, Booking.starts_at < end)
        .order_by(Booking.starts_at, Booking.table_id)
    )
    return [_booking_row(booking, label) for booking, label in result.all()]


@router.get("/calls", response_model=list[CallRow])
async def list_calls(session: Session, limit: Limit = 50) -> list[CallRow]:
    """Newest calls first. A call with no start time sorts last, by when it arrived."""
    result = await session.scalars(
        select(Call)
        .order_by(Call.started_at.desc().nulls_last(), Call.received_at.desc())
        .limit(limit)
    )
    return [_call_row(call) for call in result]


@router.get(
    "/calls/{conversation_id}",
    response_model=CallDetail,
    responses={404: {"description": "No call has this conversation ID"}},
)
async def get_call(conversation_id: str, session: Session) -> CallDetail:
    call = await session.get(Call, conversation_id)
    if call is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Call not found")
    invocations = await session.scalars(
        select(ToolInvocation)
        .where(ToolInvocation.conversation_id == conversation_id)
        .order_by(ToolInvocation.created_at, ToolInvocation.id)
    )
    row = _call_row(call)
    return CallDetail(
        conversation_id=row.conversation_id,
        agent_id=row.agent_id,
        started_at=row.started_at,
        duration_secs=row.duration_secs,
        call_successful=row.call_successful,
        summary=row.summary,
        transcript=call.transcript,
        analysis=call.analysis,
        metadata=call.call_metadata,
        tool_invocations=[_invocation_row(i) for i in invocations],
    )


@router.get("/messages", response_model=list[MessageRow])
async def list_messages(session: Session, limit: Limit = 50) -> list[MessageRow]:
    """Caller messages, newest first, handled or not."""
    result = await session.scalars(
        select(CallerMessage)
        .order_by(CallerMessage.created_at.desc(), CallerMessage.id.desc())
        .limit(limit)
    )
    return [
        MessageRow(
            id=message.id,
            conversation_id=message.conversation_id,
            caller_name=message.caller_name,
            caller_phone=message.caller_phone,
            message=message.message,
            created_at=message.created_at,
            handled_at=message.handled_at,
        )
        for message in result
    ]


@router.get("/metrics/tool-latency", response_model=list[ToolLatencyRow])
async def tool_latency(session: Session) -> list[ToolLatencyRow]:
    """Server-side p50 and p95 per tool, from requests that succeeded.

    Only 200s count. A 401 is rejected before the handler runs, and a 500 can be recorded as 0 ms
    because the app raised before it timed anything; either would pull the percentiles down and
    make a broken tool look fast.
    """
    p50 = func.percentile_cont(0.5).within_group(ToolInvocation.server_ms)
    p95 = func.percentile_cont(0.95).within_group(ToolInvocation.server_ms)
    result = await session.execute(
        select(
            ToolInvocation.tool,
            func.count().label("count"),
            p50.label("p50_ms"),
            p95.label("p95_ms"),
        )
        .where(ToolInvocation.http_status == 200)
        .group_by(ToolInvocation.tool)
        .order_by(ToolInvocation.tool)
    )
    return [
        ToolLatencyRow(tool=tool, count=count, p50_ms=p50_ms, p95_ms=p95_ms)
        for tool, count, p50_ms, p95_ms in result.all()
    ]


def _booking_row(booking: Booking, table: str) -> BookingRow:
    local = booking.starts_at.astimezone(TIMEZONE)
    return BookingRow(
        reference=booking.reference,
        table=table,
        party_size=booking.party_size,
        date=local.date(),
        time=f"{local:%H:%M}",
        starts_at=booking.starts_at.astimezone(UTC),
        guest_name=booking.guest_name,
        guest_phone=booking.guest_phone,
        notes=booking.notes,
        # The table's CHECK constraint allows only these two, so the mapping cannot hide a value.
        status="cancelled" if booking.status == "cancelled" else "confirmed",
        conversation_id=booking.conversation_id,
    )


def _call_row(call: Call) -> CallRow:
    return CallRow(
        conversation_id=call.conversation_id,
        agent_id=call.agent_id,
        started_at=call.started_at,
        duration_secs=call.duration_secs,
        call_successful=call.call_successful,
        summary=call.summary,
    )


def _invocation_row(invocation: ToolInvocation) -> ToolInvocationRow:
    return ToolInvocationRow(
        tool=invocation.tool,
        ok=invocation.ok,
        error=invocation.error,
        http_status=invocation.http_status,
        server_ms=invocation.server_ms,
        created_at=invocation.created_at,
    )
