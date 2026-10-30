"""Serving the built dashboard from the app, and what is deliberately not served.

No database: these only need the routing, which is built when the app is created.
"""

from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from maitre_d.config import Settings
from maitre_d.main import create_app


@pytest.fixture
def dist(tmp_path: Path) -> Path:
    (tmp_path / "assets").mkdir()
    (tmp_path / "index.html").write_text("<html>dashboard</html>", encoding="utf-8")
    (tmp_path / "assets" / "app.js").write_text("console.log('hi')", encoding="utf-8")
    return tmp_path


async def client_for(settings: Settings) -> AsyncIterator[AsyncClient]:
    app = create_app(settings)
    # httpx's ASGI transport doesn't run lifespan events. Building the engine opens no connection.
    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            yield client


@pytest.fixture
async def served(dist: Path) -> AsyncIterator[AsyncClient]:
    async for client in client_for(Settings(_env_file=None, frontend_dist=str(dist))):
        yield client


@pytest.fixture
async def unbuilt(tmp_path: Path) -> AsyncIterator[AsyncClient]:
    async for client in client_for(Settings(_env_file=None, frontend_dist=str(tmp_path / "nope"))):
        yield client


async def test_the_built_dashboard_is_served_from_the_root(served: AsyncClient) -> None:
    index = await served.get("/")
    asset = await served.get("/assets/app.js")

    assert index.status_code == 200
    assert "dashboard" in index.text
    assert asset.status_code == 200


async def test_the_static_files_do_not_shadow_the_api_routes(served: AsyncClient) -> None:
    """The mount is at `/`, so it must lose to every real route.

    With no secrets configured the webhook answers 500, and the dashboard routes refuse a request
    that came through a proxy: both prove the router, not the static handler, answered.
    """
    health = await served.get("/api/health")
    webhook = await served.post("/webhooks/elevenlabs", content=b"{}")
    proxied = await served.get("/api/bookings", headers={"cf-connecting-ip": "203.0.113.9"})

    assert health.json() == {"status": "ok"}
    assert webhook.status_code == 500
    assert proxied.status_code == 403


async def test_without_a_built_dashboard_nothing_is_mounted(unbuilt: AsyncClient) -> None:
    """A fresh checkout has no `frontend/dist`; the backend must still start and answer."""
    assert (await unbuilt.get("/api/health")).status_code == 200
    assert (await unbuilt.get("/")).status_code == 404


@pytest.mark.parametrize("path", ["/docs", "/redoc", "/openapi.json"])
async def test_the_interactive_docs_and_schema_are_not_served(
    served: AsyncClient, path: str
) -> None:
    """The tunnel publishes the whole app; the API's shape is exported to a file, not served."""
    response = await served.get(path)

    assert response.status_code == 404
