"""Response shapes for the dashboard API.

Kept apart from `schemas.py`, which is the contract with the voice agent. The dashboard shows more
than the agent should ever hear or say: every booking status, the transcripts, and the timing of
each tool call.
"""

import datetime as dt
from typing import Any, Literal

from pydantic import BaseModel


class BookingRow(BaseModel):
    reference: str
    table: str
    party_size: int
    date: dt.date
    # London wall-clock time, because that is what staff read off the page.
    time: str
    starts_at: dt.datetime
    guest_name: str
    guest_phone: str
    notes: str | None
    status: Literal["confirmed", "cancelled"]
    conversation_id: str | None


class CallRow(BaseModel):
    conversation_id: str
    agent_id: str
    started_at: dt.datetime | None
    duration_secs: int | None
    call_successful: str | None
    summary: str | None


class ToolInvocationRow(BaseModel):
    """One tool request as it went: no request or response bodies, which hold guests' details."""

    tool: str
    ok: bool
    error: str | None
    http_status: int
    server_ms: float
    created_at: dt.datetime


class CallDetail(CallRow):
    # The transcript is stored as the platform sent it, so it is passed through unchanged.
    transcript: list[dict[str, Any]]
    analysis: dict[str, Any] | None
    metadata: dict[str, Any] | None
    tool_invocations: list[ToolInvocationRow]


class MessageRow(BaseModel):
    id: int
    conversation_id: str
    caller_name: str
    caller_phone: str
    message: str
    created_at: dt.datetime
    handled_at: dt.datetime | None


class ToolLatencyRow(BaseModel):
    tool: str
    count: int
    p50_ms: float
    p95_ms: float
