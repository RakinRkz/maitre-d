"""What the invocation log records, what it refuses to record, and what it never logs.

The log is written by middleware around every `/tools/*` request. The local tunnel makes those
paths reachable by anyone who finds the hostname, so the rules here are about strangers as much as
about latency: they must not be able to grow the table, fill memory, or read a guest's details out
of our logs.
"""

import logging
from collections.abc import AsyncIterator
from typing import Any

import pytest
from fastapi import FastAPI
from helpers import TOOL_SECRET, booking_request, slot
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from maitre_d import bookings
from maitre_d.invocations import MAX_BODY_BYTES

OK_BODY = {"conversation_id": "conv_x", **slot()}


async def recorded(engine: AsyncEngine) -> list[Any]:
    async with engine.connect() as connection:
        result = await connection.execute(
            text("SELECT tool, http_status FROM tool_invocations ORDER BY id")
        )
        return list(result.all())


async def test_a_request_the_app_answered_is_recorded(
    client: AsyncClient, engine: AsyncEngine
) -> None:
    await client.post("/tools/check_availability", json=OK_BODY)

    assert [tuple(r) for r in await recorded(engine)] == [("check_availability", 200)]


async def test_a_refused_request_is_logged_as_a_line_and_not_stored(
    client: AsyncClient, engine: AsyncEngine, caplog: pytest.LogCaptureFixture
) -> None:
    """Anyone who finds the tunnel hostname can produce a 401, so a 401 must not cost a row.

    The line stays: a mistyped secret on the ElevenLabs side shows up as a run of these.
    """
    with caplog.at_level(logging.INFO, logger="maitre_d"):
        response = await client.post(
            "/tools/check_availability", json=OK_BODY, headers={"Authorization": "Bearer wrong"}
        )

    assert response.status_code == 401
    assert "tool=check_availability status=401" in caplog.text
    assert await recorded(engine) == []


@pytest.mark.parametrize("path", ["/tools/no_such_tool", "/tools/", "/tools/x/y/z"])
async def test_a_path_that_is_not_a_tool_is_not_stored(
    client: AsyncClient, engine: AsyncEngine, path: str
) -> None:
    """Otherwise a stranger could put any string they like into the `tool` column."""
    await client.post(path, json=OK_BODY)

    assert await recorded(engine) == []


async def test_an_oversized_body_is_refused_with_413_and_not_stored(
    client: AsyncClient, engine: AsyncEngine
) -> None:
    """Real tool bodies are a few hundred bytes. A stranger's oversized body is refused unread."""
    huge = {**OK_BODY, "padding": "x" * (MAX_BODY_BYTES + 1)}

    response = await client.post("/tools/check_availability", json=huge)

    assert response.status_code == 413
    assert await recorded(engine) == []


async def test_a_chunked_body_over_the_limit_is_refused_too(
    client: AsyncClient, engine: AsyncEngine
) -> None:
    """A chunked body declares no length up front, so the cap has to count what actually arrives."""

    async def chunks() -> AsyncIterator[bytes]:
        for _ in range(MAX_BODY_BYTES // 4096 + 2):
            yield b"x" * 4096

    response = await client.post(
        "/tools/check_availability",
        content=chunks(),
        headers={"Authorization": f"Bearer {TOOL_SECRET}"},
    )

    assert response.status_code == 413
    assert await recorded(engine) == []


async def test_a_failed_log_write_does_not_put_the_request_in_the_logs(
    client: AsyncClient, engine: AsyncEngine, caplog: pytest.LogCaptureFixture
) -> None:
    """A database error quotes the values it was given, and here those are a guest's details.

    Postgres refuses a NUL in JSONB, so a NUL in an ignored field makes the log write fail while
    the booking tool itself answers normally. The phone number sits in the same body, and must
    not reach the error log.
    """
    body = {**booking_request(), "ignored": "bad\u0000value"}

    with caplog.at_level(logging.INFO, logger="maitre_d"):
        response = await client.post("/tools/check_availability", json=body)

    assert response.status_code == 200, "a lost log write must never become the caller's error"
    assert "could not record tool invocation" in caplog.text, "the write was meant to fail"
    assert "07700" not in caplog.text
    assert "Nadia" not in caplog.text
    assert await recorded(engine) == []


async def test_a_handler_failure_is_recorded_as_a_500(
    app: FastAPI, engine: AsyncEngine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A bug in a tool is the opposite of a stranger's refusal: it is worth a row to find later."""

    async def boom(*args: object, **kwargs: object) -> None:
        raise RuntimeError("a bug in the tool")

    monkeypatch.setattr(bookings, "check_availability", boom)
    # The default transport re-raises the app's exception into the test; a real server answers 500.
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    headers = {"Authorization": f"Bearer {TOOL_SECRET}"}
    async with AsyncClient(transport=transport, base_url="http://test", headers=headers) as c:
        response = await c.post("/tools/check_availability", json=OK_BODY)

    assert response.status_code == 500
    assert [tuple(r) for r in await recorded(engine)] == [("check_availability", 500)]


@pytest.mark.parametrize(
    "headers",
    [
        pytest.param({"Authorization": "Bearer wrong"}, id="wrong-bearer"),
        # The shared client sends a valid bearer by default; an empty value overrides it.
        pytest.param({"Authorization": ""}, id="empty-bearer"),
    ],
)
async def test_a_malformed_body_without_the_bearer_is_still_a_401_and_is_not_stored(
    client: AsyncClient, engine: AsyncEngine, headers: dict[str, str]
) -> None:
    """FastAPI parses a JSON body before it runs the auth dependency, so a body that isn't JSON
    used to reach the validation handler first, which answers 200 with a friendly sentence.

    That made the auth boundary leaky for strangers and put a row in the table for each one.
    """
    response = await client.post(
        "/tools/check_availability",
        content=b"this is not json",
        headers={"Content-Type": "application/json", **headers},
    )

    assert response.status_code == 401
    assert await recorded(engine) == []


async def test_a_malformed_body_with_the_right_bearer_is_still_a_speakable_200(
    client: AsyncClient,
) -> None:
    """The agent, authenticated, still gets a sentence it can say, not a bare 4xx."""
    response = await client.post(
        "/tools/check_availability",
        content=b"this is not json",
        headers={"Content-Type": "application/json"},
    )

    assert response.status_code == 200
    assert response.json()["ok"] is False
    assert response.json()["error"] == "invalid_request"
