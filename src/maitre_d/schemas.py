"""Request and response shapes for the server tools: the contract with the agent.

The agent's tool configs in `agent/tool_configs/` describe these same fields to the LLM, and a test
keeps the two in step. Parsing is lenient where a voice transcript is predictably messy ("8pm",
"friday", "k7m 2qx") and strict where a wrong guess would book the wrong thing (dates are
YYYY-MM-DD only).
"""

import datetime as dt
import re
from typing import Annotated, Literal

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, model_validator

ErrorCode = Literal[
    "invalid_request",
    "invalid_phone",
    "party_too_large",
    "weekday_mismatch",
    "in_past",
    "too_far_ahead",
    "closed",
    "outside_hours",
    "invalid_time",
    "slot_taken",
    "already_booked_this_call",
    "not_found",
]

Weekday = Literal["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]

_ISO_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
_24_HOUR = re.compile(r"(\d{1,2}):(\d{2})(?::(\d{2}))?")
_12_HOUR = re.compile(r"(\d{1,2})(?:[:.](\d{2}))?\s*([ap])\.?\s*m\.?", re.IGNORECASE)


def _parse_date(value: object) -> dt.date:
    if isinstance(value, dt.date):
        return value
    if isinstance(value, str) and _ISO_DATE.fullmatch(value.strip()):
        return dt.date.fromisoformat(value.strip())
    raise ValueError("expected YYYY-MM-DD")


def _parse_time(value: object) -> dt.time:
    if isinstance(value, dt.time):
        return value
    if isinstance(value, str):
        text = value.strip()
        if match := _24_HOUR.fullmatch(text):
            hour, minute, second = (int(g or 0) for g in match.groups())
            return dt.time(hour, minute, second)
        if match := _12_HOUR.fullmatch(text):
            hour, minute = int(match[1]), int(match[2] or 0)
            if not 1 <= hour <= 12:
                raise ValueError("hour must be 1-12 with am or pm")
            hour = hour % 12 + (12 if match[3].lower() == "p" else 0)
            return dt.time(hour, minute)
    raise ValueError("expected HH:MM")


def _parse_weekday(value: object) -> object:
    return value.strip().capitalize() if isinstance(value, str) else value


def _parse_reference(value: object) -> object:
    # Spoken references come back spaced or hyphenated: "k7m 2qx", "K7M-2QX".
    return re.sub(r"[\s-]", "", value).upper() if isinstance(value, str) else value


LocalDate = Annotated[dt.date, BeforeValidator(_parse_date)]
LocalTime = Annotated[dt.time, BeforeValidator(_parse_time)]
WeekdayName = Annotated[Weekday, BeforeValidator(_parse_weekday)]
Reference = Annotated[str, BeforeValidator(_parse_reference), Field(min_length=4, max_length=12)]
GuestName = Annotated[str, Field(min_length=1, max_length=80)]
Phone = Annotated[str, Field(min_length=3, max_length=30)]


class ToolRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    # Bound to system__conversation_id in the tool config. The model never fills it, so it can
    # be trusted for idempotency in a way an LLM-supplied value could not.
    conversation_id: str = Field(min_length=1, max_length=200)


class SlotRequest(ToolRequest):
    date: LocalDate
    weekday: WeekdayName
    time: LocalTime
    party_size: int = Field(ge=1, le=100)


class CheckAvailabilityRequest(SlotRequest):
    pass


class CreateBookingRequest(SlotRequest):
    name: GuestName
    phone: Phone
    notes: str | None = Field(default=None, max_length=300)


class FindBookingRequest(ToolRequest):
    name: GuestName
    phone: Phone


class ChangeBookingRequest(ToolRequest):
    reference: Reference
    name: GuestName
    phone: Phone
    date: LocalDate | None = None
    weekday: WeekdayName | None = None
    time: LocalTime | None = None
    party_size: int | None = Field(default=None, ge=1, le=100)

    @model_validator(mode="after")
    def _weekday_comes_with_date(self) -> "ChangeBookingRequest":
        if self.weekday is not None and self.date is None:
            # A lone weekday used to be dropped silently, so a change to "Saturday" returned
            # "Nothing needed changing". The weekday is the cross-check, never the date.
            raise ValueError("date is required when weekday is given")
        if self.date is not None and self.weekday is None:
            raise ValueError("weekday is required when date is given")
        return self


class CancelBookingRequest(ToolRequest):
    reference: Reference
    name: GuestName
    phone: Phone


class TakeMessageRequest(ToolRequest):
    name: GuestName
    phone: Phone
    message: str = Field(min_length=1, max_length=1000)


class SlotOut(BaseModel):
    date: dt.date
    time: str
    spoken: str


class BookingOut(BaseModel):
    reference: str
    date: dt.date
    time: str
    party_size: int
    name: str
    status: Literal["confirmed", "cancelled"]
    spoken: str


class ToolReply(BaseModel):
    """What every tool returns. Unused fields are omitted from the JSON to keep replies short,
    because the model reads every token of them before it can speak."""

    ok: bool
    # `say` is read aloud to the caller; `detail` is for the model and is never spoken. Successful
    # replies omit `detail` through response_model_exclude_none=True.
    say: str
    detail: str | None = None
    error: ErrorCode | None = None
    available: bool | None = None
    slot: SlotOut | None = None
    alternatives: list[SlotOut] | None = None
    booking: BookingOut | None = None
    bookings: list[BookingOut] | None = None
