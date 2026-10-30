"""The routes with no login are refused unless the request came straight from this machine.

The local tunnel gives ElevenLabs a public hostname for the tools and the webhook, and that
hostname reaches the whole app. These tests pin both halves of the rule: what must be refused
there, and what must keep working.
"""

from collections.abc import AsyncIterator

import pytest
from fastapi import FastAPI
from helpers import TOOL_SECRET, slot
from httpx import ASGITransport, AsyncClient

UNAUTHENTICATED_ROUTES = [
    pytest.param("GET", "/api/bookings", id="bookings"),
    pytest.param("GET", "/api/calls", id="calls"),
    pytest.param("GET", "/api/calls/conv_x", id="call-detail"),
    pytest.param("GET", "/api/messages", id="messages"),
    pytest.param("GET", "/api/metrics/tool-latency", id="tool-latency"),
    pytest.param("POST", "/api/session", id="session"),
]


@pytest.fixture
async def remote(app: FastAPI) -> AsyncIterator[AsyncClient]:
    """A client whose requests arrive from another machine."""
    transport = ASGITransport(app=app, client=("203.0.113.9", 4242))
    async with AsyncClient(transport=transport, base_url="http://localhost") as client:
        yield client


@pytest.mark.parametrize(("method", "path"), UNAUTHENTICATED_ROUTES)
async def test_a_request_through_the_tunnel_is_refused(
    client: AsyncClient, method: str, path: str
) -> None:
    """Cloudflare marks everything it relays, so a stranger on the public hostname gets a 403."""
    response = await client.request(method, path, headers={"cf-connecting-ip": "203.0.113.9"})

    assert response.status_code == 403
    assert response.json() == {"detail": "Only available from this machine"}


@pytest.mark.parametrize(
    "header", ["cf-connecting-ip", "cf-ray", "x-forwarded-for", "forwarded", "x-real-ip"]
)
async def test_every_proxy_marker_counts(client: AsyncClient, header: str) -> None:
    """Any one of the headers a proxy adds is enough, so a different tunnel is caught as well.

    The names are spelled out here rather than read from the module, so that dropping one from
    the module cannot quietly drop it from the test too.
    """
    response = await client.get("/api/bookings", headers={header: "203.0.113.9"})

    assert response.status_code == 403


@pytest.mark.parametrize(("method", "path"), UNAUTHENTICATED_ROUTES)
async def test_a_request_from_another_host_is_refused(
    remote: AsyncClient, method: str, path: str
) -> None:
    """Binding to 0.0.0.0 would otherwise hand the dashboard to everyone on the network."""
    response = await remote.request(method, path)

    assert response.status_code == 403


@pytest.mark.parametrize(("method", "path"), UNAUTHENTICATED_ROUTES)
async def test_a_request_from_this_machine_is_not_refused(
    client: AsyncClient, method: str, path: str
) -> None:
    """The guard is about where the request came from; these routes answer in their own way."""
    response = await client.request(method, path)

    assert response.status_code != 403


async def test_the_tools_and_the_webhook_stay_reachable_through_the_tunnel(
    client: AsyncClient,
) -> None:
    """ElevenLabs arrives with the tunnel's headers, so those routes must not be local-only.

    Each is protected by its own secret: the tool bearer and the webhook signature.
    """
    forwarded = {"cf-connecting-ip": "203.0.113.9", "x-forwarded-for": "203.0.113.9"}

    tool = await client.post(
        "/tools/check_availability",
        json={"conversation_id": "conv_x", **slot()},
        headers={**forwarded, "Authorization": f"Bearer {TOOL_SECRET}"},
    )
    unsigned_webhook = await client.post("/webhooks/elevenlabs", content=b"{}", headers=forwarded)
    health = await client.get("/api/health", headers=forwarded)

    assert tool.status_code == 200
    assert unsigned_webhook.status_code != 403
    assert health.status_code == 200


# The operator's own browser is the other way in: a page on another site can make that browser
# talk to this server, either by rebinding its own DNS name to 127.0.0.1 or by sending requests
# cross-site. Neither goes through a tunnel, so the proxy-header check can't see them.


@pytest.mark.parametrize(
    "host",
    [
        "evil.example",
        "x.rebind.attacker.example:8000",
        "localhost.evil.example",
        "127.0.0.1.evil.example:8000",
        "localhost@evil.example",
        "",
    ],
)
@pytest.mark.parametrize(("method", "path"), UNAUTHENTICATED_ROUTES)
async def test_a_request_naming_any_host_but_localhost_is_refused(
    client: AsyncClient, method: str, path: str, host: str
) -> None:
    """DNS rebinding: the request is from loopback, but it names the attacker's host."""
    response = await client.request(method, path, headers={"Host": host})

    assert response.status_code == 403


@pytest.mark.parametrize(
    "host", ["localhost", "localhost:8000", "LOCALHOST:5173", "127.0.0.1:8000", "[::1]:8000"]
)
async def test_every_way_the_operator_reaches_this_machine_is_accepted(
    client: AsyncClient, host: str
) -> None:
    response = await client.get("/api/bookings", headers={"Host": host})

    assert response.status_code == 200


@pytest.mark.parametrize("site", ["cross-site", "same-site"])
@pytest.mark.parametrize(("method", "path"), UNAUTHENTICATED_ROUTES)
async def test_a_request_the_browser_labels_as_from_another_site_is_refused(
    client: AsyncClient, method: str, path: str, site: str
) -> None:
    """A page can't make this server mint a session token, or fetch the guest list, blind."""
    response = await client.request(method, path, headers={"Sec-Fetch-Site": site})

    assert response.status_code == 403


@pytest.mark.parametrize("site", ["same-origin", "none"])
async def test_the_dashboard_calling_its_own_backend_is_accepted(
    client: AsyncClient, site: str
) -> None:
    """The dashboard reaches /api directly or through the Vite proxy; both are same-origin."""
    response = await client.get("/api/bookings", headers={"Sec-Fetch-Site": site})

    assert response.status_code == 200
