"""Settings and the tool-auth boundary. Nothing here touches a database."""

import pytest
from fastapi import Depends, FastAPI
from httpx import ASGITransport, AsyncClient, Response
from pydantic import SecretStr

from maitre_d.config import Settings
from maitre_d.security import require_tool_bearer


def make_app(secret: str | None) -> FastAPI:
    """A minimal app with one protected route, so auth can be tested without the real routes."""
    app = FastAPI()
    app.state.settings = Settings(
        _env_file=None,
        tool_bearer_secret=None if secret is None else SecretStr(secret),
    )

    @app.get("/protected", dependencies=[Depends(require_tool_bearer)])
    async def protected() -> dict[str, str]:
        return {"status": "ok"}

    return app


async def call(app: FastAPI, authorization: str | None) -> Response:
    headers: dict[str, str] = {}
    if authorization is not None:
        headers["Authorization"] = authorization
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        return await client.get("/protected", headers=headers)


def test_settings_load_without_a_tool_bearer_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    """Alembic and seed need only the database URL, so the secret must be optional."""
    monkeypatch.delenv("TOOL_BEARER_SECRET", raising=False)
    monkeypatch.delenv("TEST_DATABASE_URL", raising=False)

    settings = Settings(_env_file=None)

    assert settings.tool_bearer_secret is None


def test_the_test_database_url_comes_from_the_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    url = "postgresql+asyncpg://u:p@h:5432/db"
    monkeypatch.setenv("TEST_DATABASE_URL", url)

    assert Settings(_env_file=None).test_database_url == url


async def test_a_correct_bearer_token_is_accepted() -> None:
    response = await call(make_app("test-secret"), "Bearer test-secret")

    assert response.status_code == 200


async def test_a_missing_bearer_token_is_rejected() -> None:
    response = await call(make_app("test-secret"), None)

    assert response.status_code == 401
    assert response.headers["WWW-Authenticate"] == "Bearer"


async def test_a_wrong_bearer_token_is_rejected() -> None:
    response = await call(make_app("test-secret"), "Bearer wrong-secret")

    assert response.status_code == 401


async def test_an_unset_secret_fails_closed_even_for_an_empty_token() -> None:
    """The old bug: an empty secret made `compare_digest(b"", b"")` authenticate an empty token."""
    response = await call(make_app(None), "Bearer ")

    assert response.status_code == 500
    assert response.json()["detail"] == "Tool authentication is not configured"


async def test_an_empty_secret_fails_closed_even_for_an_empty_token() -> None:
    response = await call(make_app(""), "Bearer ")

    assert response.status_code == 500
    assert response.json()["detail"] == "Tool authentication is not configured"
