"""`/api/session`: the browser gets a token and the API key stays on the server.

ElevenLabs is replaced by `httpx.MockTransport`, which also records what was sent, so the tests
can check both what the browser is told and what the platform was asked.
"""

import logging
from collections.abc import AsyncIterator
from dataclasses import dataclass, field

import httpx
import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from pydantic import SecretStr

from maitre_d.elevenlabs import make_client
from maitre_d.routes.session import get_elevenlabs_client

KEY = "sk-test-key-do-not-leak"
AGENT_ID = "agent_test123"
TOKEN = "tok-single-conversation-value"


@dataclass
class Platform:
    """A stand-in for ElevenLabs that records every request it receives."""

    reply: httpx.Response | Exception = field(
        default_factory=lambda: httpx.Response(
            200, json={"token": TOKEN, "conversation_id": "conv_new"}
        )
    )
    requests: list[httpx.Request] = field(default_factory=list)

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if isinstance(self.reply, Exception):
            raise self.reply
        return self.reply


def configure(app: FastAPI, **settings: object) -> None:
    app.state.settings = app.state.settings.model_copy(update=settings)


@pytest.fixture
def platform(app: FastAPI) -> Platform:
    """Voice configured, with ElevenLabs swapped for a recording stand-in."""
    configure(app, elevenlabs_api_key=SecretStr(KEY), elevenlabs_agent_id=AGENT_ID)
    stand_in = Platform()

    async def provide() -> AsyncIterator[httpx.AsyncClient]:
        async with make_client(KEY, transport=httpx.MockTransport(stand_in.handle)) as client:
            yield client

    app.dependency_overrides[get_elevenlabs_client] = provide
    return stand_in


async def test_a_session_gives_the_browser_a_token_and_nothing_else(
    client: AsyncClient, platform: Platform
) -> None:
    """The response has exactly three fields, none of which can be the API key."""
    response = await client.post("/api/session")

    assert response.status_code == 200
    assert response.json() == {
        "token": TOKEN,
        "conversation_id": "conv_new",
        "environment": None,
    }
    assert KEY not in response.text


async def test_the_api_key_is_sent_to_the_platform_for_this_agent(
    client: AsyncClient, platform: Platform
) -> None:
    """The key is used here, on the server, to ask for a token for the configured agent."""
    await client.post("/api/session")

    [request] = platform.requests
    assert request.headers["xi-api-key"] == KEY
    assert request.url.params["agent_id"] == AGENT_ID
    assert "environment" not in request.url.params


async def test_a_configured_environment_is_requested_and_reported(
    client: AsyncClient, app: FastAPI, platform: Platform
) -> None:
    """The environment decides which `backend_host` the tool URLs resolve to."""
    configure(app, elevenlabs_environment="development")

    response = await client.post("/api/session")

    assert platform.requests[0].url.params["environment"] == "development"
    assert response.json()["environment"] == "development"


@pytest.mark.parametrize("key", [None, SecretStr(""), SecretStr("   ")])
async def test_with_no_api_key_there_is_no_session_and_no_request(
    client: AsyncClient, app: FastAPI, key: SecretStr | None
) -> None:
    """An unconfigured server says so; it does not attempt a call with an empty or blank key."""
    configure(app, elevenlabs_api_key=key, elevenlabs_agent_id=AGENT_ID)

    response = await client.post("/api/session")

    assert response.status_code == 503
    assert response.json() == {"detail": "Voice is not configured"}


@pytest.mark.parametrize("agent_id", [None, "", "   "])
async def test_with_no_agent_id_there_is_no_session_and_no_request(
    client: AsyncClient, app: FastAPI, platform: Platform, agent_id: str | None
) -> None:
    configure(app, elevenlabs_agent_id=agent_id)

    response = await client.post("/api/session")

    assert response.status_code == 503
    assert platform.requests == []


@pytest.mark.parametrize("status_code", [401, 429, 500])
async def test_a_platform_failure_is_a_generic_502_with_no_reason_forwarded(
    client: AsyncClient, platform: Platform, status_code: int
) -> None:
    """The browser learns that it failed, not why. Error bodies can echo the request back."""
    platform.reply = httpx.Response(status_code, json={"detail": f"rejected {KEY} for quota"})

    response = await client.post("/api/session")

    assert response.status_code == 502
    assert response.json() == {"detail": "Could not start a voice session"}
    assert KEY not in response.text
    assert "quota" not in response.text


async def test_an_unreachable_platform_is_a_502_too(
    client: AsyncClient, platform: Platform
) -> None:
    platform.reply = httpx.ConnectError("connection refused to https://api.elevenlabs.io")

    response = await client.post("/api/session")

    assert response.status_code == 502
    assert response.json() == {"detail": "Could not start a voice session"}
    assert "elevenlabs.io" not in response.text


async def test_neither_the_token_nor_the_key_is_ever_logged(
    client: AsyncClient, platform: Platform, caplog: pytest.LogCaptureFixture
) -> None:
    """A token is a credential for a call, and the key for the whole account; logs hold neither."""
    with caplog.at_level(logging.DEBUG):
        await client.post("/api/session")
        platform.reply = httpx.Response(401, json={"detail": f"bad key {KEY}"})
        await client.post("/api/session")

    assert TOKEN not in caplog.text
    assert KEY not in caplog.text
