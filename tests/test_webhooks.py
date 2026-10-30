"""The post-call webhook: who is allowed to call it, and what it stores.

Verification is the part that matters, so most of this file is the signature matrix from the
security checklist (plan §9) run over real HTTP, plus the order of operations that makes it
meaningful: nothing is parsed until the signature over the raw bytes has been checked.
"""

import hashlib
import hmac
import json
import logging
from collections.abc import AsyncIterator, Callable
from datetime import datetime, timedelta
from typing import Any

import pytest
from fastapi import FastAPI
from helpers import NOW
from httpx import ASGITransport, AsyncClient, Response
from pydantic import SecretStr
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from maitre_d.models import Call
from maitre_d.routes.webhooks import MAX_BODY_BYTES
from maitre_d.security import WebhookSignatureError, verify_webhook_signature

SECRET = "test-webhook-secret"
URL = "/webhooks/elevenlabs"
STARTED = NOW - timedelta(minutes=2)


def sign(body: bytes, *, secret: str = SECRET, at: datetime = NOW) -> str:
    """A header the way the platform builds it: HMAC-SHA256 over "<t>.<raw body>"."""
    t = str(int(at.timestamp()))
    digest = hmac.new(secret.encode(), t.encode() + b"." + body, hashlib.sha256).hexdigest()
    return f"t={t},v0={digest}"


def event(conversation_id: str = "conv_abc", **data: Any) -> dict[str, Any]:
    """A post_call_transcription event shaped like the platform's documented example."""
    payload: dict[str, Any] = {
        "agent_id": "agent_test",
        "conversation_id": conversation_id,
        "status": "done",
        "transcript": [
            {
                "role": "agent",
                "message": "Saffron Table, how can I help?",
                "tool_calls": None,
                "tool_results": None,
                "time_in_call_secs": 0,
            },
            {
                "role": "user",
                "message": "A table for two on Friday, please.",
                "tool_calls": None,
                "tool_results": None,
                "time_in_call_secs": 3,
            },
        ],
        "metadata": {
            "start_time_unix_secs": int(STARTED.timestamp()),
            "call_duration_secs": 42,
        },
        "analysis": {
            "call_successful": "success",
            "transcript_summary": "The caller booked a table for two.",
        },
    }
    payload.update(data)
    return {
        "type": "post_call_transcription",
        "event_timestamp": int(NOW.timestamp()),
        "data": payload,
    }


def compact(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload, separators=(",", ":")).encode()


@pytest.fixture
async def hook(app: FastAPI) -> AsyncIterator[AsyncClient]:
    """A client with no tool bearer, against an app that has a webhook secret configured."""
    app.state.settings = app.state.settings.model_copy(
        update={"elevenlabs_webhook_secret": SecretStr(SECRET)}
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client


async def deliver(hook: AsyncClient, body: bytes, signature: str | None) -> Response:
    headers = {"Content-Type": "application/json"}
    if signature is not None:
        headers["ElevenLabs-Signature"] = signature
    return await hook.post(URL, content=body, headers=headers)


async def stored_calls(session: AsyncSession) -> list[Call]:
    session.expire_all()
    return list((await session.scalars(select(Call).order_by(Call.conversation_id))).all())


async def test_a_correctly_signed_event_is_stored(hook: AsyncClient, session: AsyncSession) -> None:
    """The happy path, and every column the dashboard reads comes out of the payload."""
    body = compact(event())

    response = await deliver(hook, body, sign(body))

    assert response.status_code == 200
    assert response.json() == {"status": "stored"}
    [call] = await stored_calls(session)
    assert call.conversation_id == "conv_abc"
    assert call.agent_id == "agent_test"
    assert call.started_at == STARTED
    assert call.duration_secs == 42
    assert call.call_successful == "success"
    assert call.summary == "The caller booked a table for two."
    assert call.transcript == event()["data"]["transcript"]
    assert call.call_metadata == event()["data"]["metadata"]
    assert call.analysis == event()["data"]["analysis"]


async def test_a_redelivered_event_updates_the_one_row(
    hook: AsyncClient, session: AsyncSession
) -> None:
    """Retries send the same event again; the call must end up as one row, not two or an error.

    A redelivery carrying refined analysis replaces the old values, but `received_at` stays at
    the first time we heard about the call.
    """
    first = compact(event())
    assert (await deliver(hook, first, sign(first))).status_code == 200
    [before] = await stored_calls(session)
    first_received = before.received_at

    refined = compact(
        event(analysis={"call_successful": "failure", "transcript_summary": "Revised summary."})
    )
    assert (await deliver(hook, refined, sign(refined))).status_code == 200

    [after] = await stored_calls(session)
    assert after.summary == "Revised summary."
    assert after.call_successful == "failure"
    assert after.received_at == first_received


@pytest.mark.parametrize(
    "header",
    [
        pytest.param(lambda body: sign(body, secret="someone-elses-secret"), id="wrong-secret"),
        pytest.param(lambda body: sign(body, at=NOW - timedelta(minutes=31)), id="stale"),
        pytest.param(lambda body: sign(body, at=NOW + timedelta(minutes=31)), id="future-dated"),
        pytest.param(lambda body: None, id="missing-header"),
        pytest.param(lambda body: "not a signature header", id="malformed-header"),
        pytest.param(lambda body: f"v0={'0' * 64}", id="no-timestamp"),
        pytest.param(lambda body: f"t={int(NOW.timestamp())}", id="no-signature"),
        pytest.param(lambda body: "t=abc,v0=" + "0" * 64, id="non-numeric-timestamp"),
        pytest.param(lambda body: sign(body).replace("v0=", "v1="), id="unknown-scheme"),
    ],
)
async def test_a_request_that_fails_verification_is_refused_and_stores_nothing(
    hook: AsyncClient, session: AsyncSession, header: Callable[[bytes], str | None]
) -> None:
    """Each way a header can be wrong gets the same 401, and nothing reaches the database."""
    body = compact(event())

    response = await deliver(hook, body, header(body))

    assert response.status_code == 401
    assert response.json() == {"detail": "Invalid webhook signature"}
    assert await stored_calls(session) == []


async def test_a_body_changed_after_signing_is_refused(
    hook: AsyncClient, session: AsyncSession
) -> None:
    """A valid signature for one body is worthless for another: tampering is detected."""
    signed = compact(event())
    tampered = compact(event(analysis={"call_successful": "failure", "transcript_summary": "x"}))

    response = await deliver(hook, tampered, sign(signed))

    assert response.status_code == 401
    assert await stored_calls(session) == []


@pytest.mark.parametrize("offset", [timedelta(minutes=30), -timedelta(minutes=30)])
async def test_a_timestamp_exactly_at_the_edge_of_the_window_is_accepted(
    hook: AsyncClient, offset: timedelta
) -> None:
    """The window is thirty minutes either side, inclusive, so clock skew within it is tolerated."""
    body = compact(event())

    response = await deliver(hook, body, sign(body, at=NOW + offset))

    assert response.status_code == 200


async def test_the_signature_covers_the_raw_bytes_not_the_parsed_json(
    hook: AsyncClient,
) -> None:
    """Whitespace is part of what was signed.

    The same JSON, indented, is accepted when it is the indented bytes that were signed and
    refused when the compact form was. A verifier that parsed and re-serialised first would get
    both of these wrong.
    """
    payload = event()
    compact_bytes = compact(payload)
    indented_bytes = json.dumps(payload, indent=2).encode()

    assert (await deliver(hook, indented_bytes, sign(indented_bytes))).status_code == 200
    assert (await deliver(hook, indented_bytes, sign(compact_bytes))).status_code == 401


async def test_the_signature_is_checked_before_the_body_is_parsed(hook: AsyncClient) -> None:
    """Unreadable JSON from a stranger is a 401, not a 400 that shows we tried to parse it.

    The same unreadable body with a genuine signature does reach the parser, which is how a
    platform change shows up as a loud failure.
    """
    body = b"this is not json"

    stranger = await deliver(hook, body, sign(body, secret="someone-elses-secret"))
    platform = await deliver(hook, body, sign(body))

    assert stranger.status_code == 401
    assert platform.status_code == 400


@pytest.mark.parametrize("secret", [None, SecretStr(""), SecretStr("   ")])
async def test_with_no_secret_configured_every_request_fails_closed(
    hook: AsyncClient, app: FastAPI, session: AsyncSession, secret: SecretStr | None
) -> None:
    """An unset or blank secret must never authenticate a signature made with an empty key."""
    app.state.settings = app.state.settings.model_copy(update={"elevenlabs_webhook_secret": secret})
    body = compact(event())

    response = await deliver(hook, body, sign(body, secret=""))

    assert response.status_code == 500
    assert await stored_calls(session) == []


async def test_other_event_types_are_acknowledged_and_ignored(
    hook: AsyncClient, session: AsyncSession
) -> None:
    """A 200 for what we don't handle, so the platform doesn't retry it or count it a failure."""
    audio = compact(
        {
            "type": "post_call_audio",
            "event_timestamp": int(NOW.timestamp()),
            "data": {"agent_id": "agent_test", "conversation_id": "conv_abc", "full_audio": "AAAA"},
        }
    )
    unknown = compact({"type": "something_new", "event_timestamp": 1})

    for body in (audio, unknown):
        response = await deliver(hook, body, sign(body))
        assert response.status_code == 200
        assert response.json() == {"status": "ignored"}
    assert await stored_calls(session) == []


async def test_a_signed_transcript_missing_required_fields_fails_loudly(
    hook: AsyncClient, session: AsyncSession
) -> None:
    """The sender is proven genuine, so a payload we can't use is a platform change to notice."""
    payload = event()
    del payload["data"]["conversation_id"]
    body = compact(payload)

    response = await deliver(hook, body, sign(body))

    assert response.status_code == 422
    assert await stored_calls(session) == []


async def test_a_payload_without_metadata_or_analysis_is_stored_with_empty_fields(
    hook: AsyncClient, session: AsyncSession
) -> None:
    """Optional parts may be absent: the call is still worth keeping, with nothing invented."""
    body = compact(event(metadata=None, analysis=None))

    assert (await deliver(hook, body, sign(body))).status_code == 200

    [call] = await stored_calls(session)
    assert (call.started_at, call.duration_secs, call.call_successful, call.summary) == (
        None,
        None,
        None,
        None,
    )


async def test_a_boolean_is_not_mistaken_for_a_duration(
    hook: AsyncClient, session: AsyncSession
) -> None:
    """bool is an int in Python, so `true` would otherwise be stored as a one-second call."""
    body = compact(event(metadata={"call_duration_secs": True}))

    assert (await deliver(hook, body, sign(body))).status_code == 200

    [call] = await stored_calls(session)
    assert call.duration_secs is None


async def test_an_oversized_body_is_refused_before_it_is_verified(hook: AsyncClient) -> None:
    """The whole body has to be read before anyone is authenticated, so it is bounded first."""
    body = b"x" * (MAX_BODY_BYTES + 1)

    response = await deliver(hook, body, signature=None)

    assert response.status_code == 413


async def test_a_chunked_body_over_the_limit_is_refused_too(hook: AsyncClient) -> None:
    """A chunked body declares no length, so the cap has to be enforced while reading."""

    async def chunks() -> AsyncIterator[bytes]:
        for _ in range(MAX_BODY_BYTES // 65536 + 2):
            yield b"x" * 65536

    response = await hook.post(URL, content=chunks(), headers={"ElevenLabs-Signature": "t=1,v0=0"})

    assert response.status_code == 413


async def test_what_the_caller_said_is_not_logged_above_debug(
    hook: AsyncClient, caplog: pytest.LogCaptureFixture
) -> None:
    """A rejected payload is logged by its field paths, never by its values.

    A validation error quotes the input it rejected. Here the rejected input is the caller's own
    words (a transcript that is a string, not a list), so logging the error itself would leak them.
    """
    body = compact(event(transcript="my number is 07700 900123"))

    with caplog.at_level(logging.INFO, logger="maitre_d"):
        response = await deliver(hook, body, sign(body))

    assert response.status_code == 422
    assert "07700" not in caplog.text
    assert "transcript" in caplog.text


def test_any_matching_candidate_signature_is_enough() -> None:
    """The header may carry more than one v0 value, so a secret can be rotated without downtime."""
    body = b"{}"
    good = sign(body).split("v0=")[1]
    header = f"t={int(NOW.timestamp())},v0={'0' * 64},v0={good}"

    verify_webhook_signature(body, header, SECRET, NOW)


def test_a_non_ascii_signature_is_a_failed_check_not_a_crash() -> None:
    """hmac.compare_digest raises TypeError on a non-ASCII str; that must not become a 500."""
    header = f"t={int(NOW.timestamp())},v0=é{'0' * 63}"

    with pytest.raises(WebhookSignatureError):
        verify_webhook_signature(b"{}", header, SECRET, NOW)


def test_digits_from_other_scripts_are_not_a_timestamp() -> None:
    """str.isdigit accepts Arabic-Indic digits; a signed header only ever has ASCII ones."""
    header = f"t={'١٢٣٤٥٦٧٨٩٠'},v0={'0' * 64}"

    with pytest.raises(WebhookSignatureError, match="timestamp"):
        verify_webhook_signature(b"{}", header, SECRET, NOW)


def test_one_second_past_the_window_is_refused() -> None:
    """Just outside the tolerance fails even with a valid signature, on both sides."""
    body = b"{}"
    for offset in (timedelta(minutes=30, seconds=1), -timedelta(minutes=30, seconds=1)):
        with pytest.raises(WebhookSignatureError, match="tolerance"):
            verify_webhook_signature(body, sign(body, at=NOW + offset), SECRET, NOW)


async def test_the_request_headers_of_a_tool_call_are_never_stored(
    hook: AsyncClient, session: AsyncSession
) -> None:
    """A tool call's details can include the headers we sent, and one of them is the bearer token.

    Whatever the platform does about redacting it, the secret must not land in `calls`, which the
    dashboard returns as stored. The rest of the call is kept.
    """
    leaky_turn = {
        "role": "agent",
        "message": None,
        "time_in_call_secs": 5,
        "tool_calls": [
            {
                "request_id": "req_1",
                "tool_name": "check_availability",
                "params_as_json": '{"date": "2026-10-23"}',
                "tool_has_been_called": True,
                "tool_details": {
                    "type": "webhook",
                    "method": "POST",
                    "url": "https://example.trycloudflare.com/tools/check_availability",
                    "headers": {"Authorization": "Bearer s3cret-tool-token"},
                },
            },
            "not a dict, kept as received",
        ],
    }
    body = compact(event(transcript=[leaky_turn]))

    assert (await deliver(hook, body, sign(body))).status_code == 200

    [call] = await stored_calls(session)
    stored = json.dumps(call.transcript)
    assert "s3cret-tool-token" not in stored
    assert "Authorization" not in stored
    [stored_call, odd] = call.transcript[0]["tool_calls"]
    assert stored_call["tool_details"]["url"].endswith("/tools/check_availability")
    assert stored_call["params_as_json"] == '{"date": "2026-10-23"}'
    assert odd == "not a dict, kept as received"
