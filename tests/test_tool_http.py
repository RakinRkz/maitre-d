"""What the tool endpoints do at the HTTP level: auth, malformed calls, and the latency log."""

from typing import Any

from helpers import TOOL_SECRET, booking_request, slot
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine


async def test_a_call_without_the_bearer_token_is_refused(client: AsyncClient) -> None:
    response = await client.post(
        "/tools/check_availability",
        json={"conversation_id": "c", **slot()},
        headers={"Authorization": ""},
    )
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"


async def test_a_call_with_the_wrong_token_is_refused(client: AsyncClient) -> None:
    response = await client.post(
        "/tools/check_availability",
        json={"conversation_id": "c", **slot()},
        headers={"Authorization": f"Bearer {TOOL_SECRET}x"},
    )
    assert response.status_code == 401


async def test_an_unauthenticated_malformed_call_learns_nothing_about_the_schema(
    client: AsyncClient,
) -> None:
    """Auth runs before body validation, so the field hints are only for the real agent."""
    response = await client.post(
        "/tools/create_booking", json={"time": "eight"}, headers={"Authorization": ""}
    )
    assert response.status_code == 401


async def test_a_malformed_call_gets_a_caller_sentence_and_model_detail(
    client: AsyncClient,
) -> None:
    response = await client.post(
        "/tools/check_availability",
        json={"conversation_id": "c", **slot(time="around eight", date="30/10/2026")},
    )
    assert response.status_code == 200
    reply = response.json()
    assert reply["ok"] is False
    assert reply["error"] == "invalid_request"
    assert reply["say"] == "Sorry, I didn't catch that. Could you say it again?"
    assert "date must be YYYY-MM-DD, for example 2026-10-30" in reply["detail"]
    assert "time must be HH:MM in 24-hour London time, for example 20:00" in reply["detail"]


async def test_replies_leave_out_fields_that_do_not_apply(client: AsyncClient) -> None:
    """The model reads every token of a reply before it speaks, so empty fields cost time."""
    reply: dict[str, Any] = (
        await client.post("/tools/create_booking", json=booking_request())
    ).json()
    assert set(reply) == {"ok", "booking", "say"}


async def test_every_tool_call_is_logged_with_its_server_time(
    client: AsyncClient, engine: AsyncEngine
) -> None:
    await client.post("/tools/create_booking", json=booking_request(conversation_id="conv_logged"))
    await client.post(
        "/tools/check_availability",
        json={"conversation_id": "c", **slot()},
        headers={"Authorization": ""},
    )

    async with engine.connect() as connection:
        rows = (
            await connection.execute(
                text(
                    "SELECT conversation_id, tool, ok, http_status, server_ms, "
                    "response->>'say' AS say FROM tool_invocations ORDER BY id"
                )
            )
        ).all()

    booked, refused = rows
    assert (booked.conversation_id, booked.tool, booked.ok, booked.http_status) == (
        "conv_logged",
        "create_booking",
        True,
        200,
    )
    assert booked.server_ms > 0
    assert booked.say.startswith("Booked:")
    assert (refused.tool, refused.ok, refused.http_status) == ("check_availability", False, 401)
