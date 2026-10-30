"""The post-call webhook: ElevenLabs tells us a conversation has finished.

The order is the point of this module: read the raw bytes (bounded), verify the signature over
exactly those bytes, and only then parse them. Parsing first would mean running a JSON parser on
whatever an unauthenticated caller sends, and re-serialising what it produced would no longer be
the bytes that were signed.
"""

import logging
from datetime import UTC, datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from maitre_d.db import get_session
from maitre_d.models import Call
from maitre_d.routes.tools import get_now
from maitre_d.security import (
    WebhookSignatureError,
    require_webhook_secret,
    verify_webhook_signature,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/webhooks", tags=["webhooks"])

# The signature covers the whole body, so the whole body must be read before anyone is
# authenticated. This bounds what an unauthenticated caller can make us buffer. A transcript is a
# few kilobytes; a post-call audio event (not subscribed to) would be far larger and is refused.
MAX_BODY_BYTES = 2 * 1024 * 1024

POST_CALL_TRANSCRIPTION = "post_call_transcription"

Session = Annotated[AsyncSession, Depends(get_session)]
Now = Annotated[datetime, Depends(get_now)]
WebhookSecret = Annotated[str, Depends(require_webhook_secret)]


class WebhookEvent(BaseModel):
    """The envelope every event shares. `data` stays loose because its shape depends on `type`."""

    type: str
    data: dict[str, Any] = Field(default_factory=dict)


class PostCallTranscription(BaseModel):
    """The parts of a `post_call_transcription` that we store. Everything else is ignored."""

    conversation_id: str = Field(min_length=1)
    agent_id: str = Field(min_length=1)
    transcript: list[dict[str, Any]]
    metadata: dict[str, Any] | None = None
    analysis: dict[str, Any] | None = None


class WebhookAck(BaseModel):
    status: Literal["stored", "ignored"]


async def _read_body(request: Request) -> bytes:
    chunks: list[bytes] = []
    size = 0
    # Counted as it arrives rather than trusting Content-Length, which a chunked body doesn't
    # have and a dishonest one can understate.
    async for chunk in request.stream():
        size += len(chunk)
        if size > MAX_BODY_BYTES:
            raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, detail="Webhook body too large")
        chunks.append(chunk)
    return b"".join(chunks)


def _number(value: object) -> float | None:
    # bool is an int subclass; a JSON `true` is not a duration.
    return float(value) if isinstance(value, int | float) and not isinstance(value, bool) else None


def _started_at(metadata: dict[str, Any] | None) -> datetime | None:
    seconds = _number((metadata or {}).get("start_time_unix_secs"))
    if seconds is None:
        return None
    try:
        return datetime.fromtimestamp(seconds, UTC)
    except (OverflowError, OSError, ValueError):
        return None


def _without_request_headers(transcript: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The transcript with each tool call's outgoing request headers removed.

    A tool call's `tool_details` can carry the headers we sent, and one of them is the bearer
    token. The platform may redact it; that is not something to find out by storing it. We never
    need the headers, and the dashboard returns the transcript as stored.
    """
    cleaned: list[dict[str, Any]] = []
    for turn in transcript:
        calls = turn.get("tool_calls")
        if isinstance(calls, list):
            turn = {**turn, "tool_calls": [_without_headers(call) for call in calls]}
        cleaned.append(turn)
    return cleaned


def _without_headers(call: Any) -> Any:
    details = call.get("tool_details") if isinstance(call, dict) else None
    if not isinstance(details, dict) or "headers" not in details:
        return call
    return {**call, "tool_details": {k: v for k, v in details.items() if k != "headers"}}


def _text(source: dict[str, Any] | None, key: str) -> str | None:
    value = (source or {}).get(key)
    return value if isinstance(value, str) else None


@router.post("/elevenlabs", response_model=WebhookAck)
async def elevenlabs_webhook(
    request: Request,
    session: Session,
    now: Now,
    secret: WebhookSecret,
    elevenlabs_signature: Annotated[str | None, Header()] = None,
) -> WebhookAck:
    raw_body = await _read_body(request)

    try:
        verify_webhook_signature(raw_body, elevenlabs_signature, secret, now)
    except WebhookSignatureError as error:
        # The reason is ours to read; the sender is told nothing about which check failed.
        logger.info("post-call webhook rejected: %s", error.reason)
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED, detail="Invalid webhook signature"
        ) from None

    # Past this point the sender is proven to be ElevenLabs, so a body we cannot read means the
    # platform changed something. That deserves a loud failure, not a quiet 200.
    try:
        event = WebhookEvent.model_validate_json(raw_body)
    except ValidationError:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="Malformed webhook body") from None

    if event.type != POST_CALL_TRANSCRIPTION:
        return WebhookAck(status="ignored")

    try:
        call = PostCallTranscription.model_validate(event.data)
    except ValidationError as error:
        # Field paths only: a validation error also quotes the offending values, which here
        # include what the caller said.
        fields = [".".join(str(part) for part in e["loc"]) for e in error.errors()]
        logger.warning("post-call webhook is missing or has invalid fields: %s", fields)
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, detail="Unexpected webhook payload"
        ) from None

    duration = _number((call.metadata or {}).get("call_duration_secs"))
    statement = insert(Call).values(
        conversation_id=call.conversation_id,
        agent_id=call.agent_id,
        started_at=_started_at(call.metadata),
        duration_secs=None if duration is None else round(duration),
        call_successful=_text(call.analysis, "call_successful"),
        summary=_text(call.analysis, "transcript_summary"),
        transcript=_without_request_headers(call.transcript),
        analysis=call.analysis,
        call_metadata=call.metadata,
    )
    # Retries and redeliveries send the same event, so storing is an upsert: the row ends up
    # identical however many times it arrives. `received_at` is left alone so it stays the first
    # time we heard about the call.
    refreshed = {
        column: statement.excluded[column]
        for column in (
            "agent_id",
            "started_at",
            "duration_secs",
            "call_successful",
            "summary",
            "transcript",
            "analysis",
            "metadata",
        )
    }
    await session.execute(
        statement.on_conflict_do_update(index_elements=["conversation_id"], set_=refreshed)
    )
    await session.commit()

    logger.info("post-call webhook stored a call with %d turns", len(call.transcript))
    logger.debug("stored call %s", call.conversation_id)
    return WebhookAck(status="stored")
