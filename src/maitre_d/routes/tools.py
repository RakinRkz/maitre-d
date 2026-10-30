"""The server tools: the endpoints the ElevenLabs agent calls mid-conversation.

Each path is the tool's name as the agent knows it. Every route answers 200 with a `ToolReply`
for any business outcome, including "fully booked" and "not found"; only a bad token (401) or a
bug (500) is anything else. See docs/decisions.md #4.
"""

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from maitre_d import bookings, speech
from maitre_d.db import get_session
from maitre_d.schemas import (
    CancelBookingRequest,
    ChangeBookingRequest,
    CheckAvailabilityRequest,
    CreateBookingRequest,
    FindBookingRequest,
    TakeMessageRequest,
    ToolReply,
)
from maitre_d.security import require_tool_bearer


def get_now() -> datetime:
    """The current instant. A dependency so tests can fix the clock."""
    return datetime.now(UTC)


Session = Annotated[AsyncSession, Depends(get_session)]
Now = Annotated[datetime, Depends(get_now)]

router = APIRouter(prefix="/tools", tags=["tools"], dependencies=[Depends(require_tool_bearer)])


@router.post("/check_availability", response_model_exclude_none=True)
async def check_availability(
    body: CheckAvailabilityRequest, session: Session, now: Now
) -> ToolReply:
    return await bookings.check_availability(session, body, now)


@router.post("/create_booking", response_model_exclude_none=True)
async def create_booking(body: CreateBookingRequest, session: Session, now: Now) -> ToolReply:
    return await bookings.create_booking(session, body, now)


@router.post("/find_booking", response_model_exclude_none=True)
async def find_booking(body: FindBookingRequest, session: Session, now: Now) -> ToolReply:
    return await bookings.find_booking(session, body, now)


@router.post("/change_booking", response_model_exclude_none=True)
async def change_booking(body: ChangeBookingRequest, session: Session, now: Now) -> ToolReply:
    return await bookings.change_booking(session, body, now)


@router.post("/cancel_booking", response_model_exclude_none=True)
async def cancel_booking(body: CancelBookingRequest, session: Session, now: Now) -> ToolReply:
    return await bookings.cancel_booking(session, body, now)


@router.post("/take_message", response_model_exclude_none=True)
async def take_message(body: TakeMessageRequest, session: Session, now: Now) -> ToolReply:
    return await bookings.take_message(session, body, now)


# What the model should do differently, per field. FastAPI's own 422 body is written for a
# developer; this is written for an LLM that has to fix its next call.
FIELD_HINTS = {
    "date": "date must be YYYY-MM-DD, for example 2026-10-30",
    "time": "time must be HH:MM in 24-hour London time, for example 20:00",
    "weekday": "weekday must be the name of the day the caller said, for example Friday",
    "party_size": "party_size must be a whole number of guests",
    "name": "name is required",
    "phone": "phone is required",
    "reference": "reference is the booking reference returned by find_booking",
    "message": "message is required",
    "notes": "notes must be under 300 characters",
    "conversation_id": "conversation_id is missing, so the tool is misconfigured",
}


def _describe(error: dict[str, object]) -> str:
    location = error.get("loc", ())
    field = location[-1] if isinstance(location, tuple) and len(location) > 1 else None
    if isinstance(field, str) and field in FIELD_HINTS:
        return FIELD_HINTS[field]
    return str(error.get("msg", "invalid request")).removeprefix("Value error, ")


async def tool_validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
    problems = list(dict.fromkeys(_describe(e) for e in exc.errors()))
    # The caller hears one short sentence; the field hints are for the model and go in `detail`.
    reply = ToolReply(
        ok=False,
        error="invalid_request",
        say=speech.invalid_request(),
        detail="; ".join(problems),
    )
    return JSONResponse(reply.model_dump(mode="json", exclude_none=True))
