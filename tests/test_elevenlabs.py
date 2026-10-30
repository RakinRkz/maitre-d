"""The ElevenLabs REST calls and the backend-host validator, with no network and no database.

Every request goes through httpx.MockTransport, so nothing reaches api.elevenlabs.io. The key
tests matter most: the platform can echo a request back in an error body, and an error message is
the easiest place for the key to end up in a log.
"""

import importlib.util
import json
from collections.abc import Callable
from pathlib import Path
from types import ModuleType
from typing import Any

import httpx
import pytest

from maitre_d.elevenlabs import (
    ConversationToken,
    ElevenLabsError,
    ensure_text_document,
    ensure_text_document_outcome,
    get_conversation_token,
    make_client,
    set_environment_variable,
)

API_KEY = "xi-test-key-not-a-real-key"
MENU = "Starters\n- Soup of the day\n"
KNOWLEDGE_BASE = "/v1/convai/knowledge-base"
ENVIRONMENT_VARIABLES = "/v1/convai/environment-variables"

Handler = Callable[[httpx.Request], httpx.Response]


def load_script(name: str) -> ModuleType:
    """Import a file from scripts/. That directory is not a package, so it is loaded by path."""
    path = Path(__file__).resolve().parents[1] / "scripts" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


validate_backend_host: Callable[[str], str] = load_script("set_backend_host").validate_backend_host


def client_with(handler: Handler, seen: list[httpx.Request]) -> httpx.AsyncClient:
    """A client whose requests all go to `handler`. Each request is also appended to `seen`."""

    def record(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return handler(request)

    return make_client(API_KEY, transport=httpx.MockTransport(record))


def knowledge_base(
    documents: dict[str, tuple[str, str]], seen: list[httpx.Request]
) -> httpx.AsyncClient:
    """A client backed by a fake knowledge base. `documents` maps document id to (name, text).

    The fake matches names by prefix, as the platform's `search` does, so a test meets the same
    near-miss names the real endpoint returns.
    """

    def handle(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if request.method == "GET" and path == KNOWLEDGE_BASE:
            prefix = request.url.params.get("search", "")
            found = [
                {"id": document_id, "name": name, "type": "text"}
                for document_id, (name, _text) in documents.items()
                if name.startswith(prefix)
            ]
            listing = {"documents": found, "has_more": False, "next_cursor": None}
            return httpx.Response(200, json=listing)
        if request.method == "GET" and path.endswith("/content"):
            document_id = path.removeprefix(f"{KNOWLEDGE_BASE}/").removesuffix("/content")
            return httpx.Response(200, text=documents[document_id][1])
        if request.method == "POST" and path == f"{KNOWLEDGE_BASE}/text":
            name = json.loads(request.content)["name"]
            return httpx.Response(200, json={"id": "doc_new", "name": name})
        return httpx.Response(404, json={"detail": "not expected by this test"})

    return client_with(handle, seen)


def variable_listing(*variables: dict[str, Any]) -> httpx.Response:
    listing = {"environment_variables": list(variables), "has_more": False, "next_cursor": None}
    return httpx.Response(200, json=listing)


def request_body(request: httpx.Request) -> Any:
    body: Any = json.loads(request.content)
    return body


async def test_the_token_request_sends_the_key_and_agent_and_reads_the_answer() -> None:
    """The key goes in the header, the agent in the query, and both answer fields come back."""
    seen: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"token": "tok_123", "conversation_id": "conv_456"})

    async with client_with(handle, seen) as client:
        token = await get_conversation_token(client, agent_id="agent_789")

    [request] = seen
    assert request.method == "GET"
    assert request.url.path == "/v1/convai/conversation/token"
    assert request.headers["xi-api-key"] == API_KEY
    assert request.url.params["agent_id"] == "agent_789"
    assert token == ConversationToken(token="tok_123", conversation_id="conv_456")


async def test_the_environment_is_sent_only_when_the_caller_names_one() -> None:
    """Left out, the platform uses its own default (production), so we must not send one."""
    seen: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"token": "tok", "conversation_id": "conv"})

    async with client_with(handle, seen) as client:
        await get_conversation_token(client, agent_id="agent_789")
        await get_conversation_token(client, agent_id="agent_789", environment="staging")

    without, with_environment = seen
    assert "environment" not in without.url.params
    assert with_environment.url.params["environment"] == "staging"


def test_the_token_is_left_out_of_its_repr() -> None:
    """A traceback or log line that prints the token object must not reveal the token."""
    token = ConversationToken(token="tok_secret_value", conversation_id="conv_1")

    assert "tok_secret_value" not in repr(token)
    assert "conv_1" in repr(token)


async def test_a_401_names_the_status_and_keeps_the_key_out_even_when_the_body_echoes_it() -> None:
    """The platform's message is kept, the key is removed from it, and no request header appears."""
    seen: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        body = {"detail": {"status": "invalid_api_key", "message": f"{API_KEY} was rejected."}}
        return httpx.Response(401, json=body)

    async with client_with(handle, seen) as client:
        with pytest.raises(ElevenLabsError) as info:
            await get_conversation_token(client, agent_id="agent_789")

    message = str(info.value)
    assert info.value.status_code == 401
    assert "401" in message
    assert "invalid_api_key" in message
    assert API_KEY not in message
    assert "xi-api-key" not in message.lower()


async def test_a_500_keeps_the_key_out_when_the_error_body_echoes_it() -> None:
    """A server error can echo the request. The message still gives the status, not the key."""
    seen: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"detail": f"upstream failed for key {API_KEY}"})

    async with client_with(handle, seen) as client:
        with pytest.raises(ElevenLabsError) as info:
            await get_conversation_token(client, agent_id="agent_789")

    message = str(info.value)
    assert info.value.status_code == 500
    assert "500" in message
    assert API_KEY not in message


async def test_an_identical_document_is_reused_instead_of_uploaded_again() -> None:
    """Running the upload again with unchanged files must not add another copy of the document."""
    seen: list[httpx.Request] = []

    async with knowledge_base({"doc_1": ("menu.md", MENU)}, seen) as client:
        document_id = await ensure_text_document(client, name="menu.md", text=MENU)

    assert document_id == "doc_1"
    assert not any(request.method == "POST" for request in seen)


async def test_changed_content_creates_a_new_document_and_leaves_the_old_one_in_place() -> None:
    """Changed text creates a new document. The old one stays for any agent still pointing at it."""
    seen: list[httpx.Request] = []

    async with knowledge_base({"doc_1": ("menu.md", "old menu")}, seen) as client:
        outcome = await ensure_text_document_outcome(client, name="menu.md", text="new menu")

    assert outcome == ("doc_new", True)
    [upload] = [request for request in seen if request.method == "POST"]
    assert json.loads(upload.content) == {"text": "new menu", "name": "menu.md"}


async def test_a_document_is_created_when_none_has_that_name() -> None:
    """The first upload of a file has nothing to reuse, so it creates the document."""
    seen: list[httpx.Request] = []

    async with knowledge_base({}, seen) as client:
        document_id = await ensure_text_document(client, name="menu.md", text=MENU)

    assert document_id == "doc_new"


async def test_a_longer_name_that_starts_the_same_way_is_not_reused() -> None:
    """Search matches by prefix: "menu.md" also finds "menu.md.bak". Only the exact name counts."""
    seen: list[httpx.Request] = []

    async with knowledge_base({"doc_1": ("menu.md.bak", MENU)}, seen) as client:
        document_id = await ensure_text_document(client, name="menu.md", text=MENU)

    assert document_id == "doc_new"


@pytest.mark.parametrize(
    "documents",
    [
        pytest.param({"doc_1": ("menu.md", MENU)}, id="identical"),
        pytest.param({"doc_1": ("menu.md", "old menu")}, id="different"),
        pytest.param({}, id="absent"),
    ],
)
async def test_ensure_never_deletes_or_edits_a_document_in_any_case(
    documents: dict[str, tuple[str, str]],
) -> None:
    """Nothing on the upload path may delete a knowledge-base document or change one in place."""
    seen: list[httpx.Request] = []

    async with knowledge_base(documents, seen) as client:
        await ensure_text_document(client, name="menu.md", text=MENU)

    assert {request.method for request in seen} <= {"GET", "POST"}


async def test_a_missing_label_creates_the_variable_with_a_production_value() -> None:
    """The platform needs a production value to create a variable, so the first write sends one."""
    seen: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return variable_listing()
        return httpx.Response(200, json={"id": "env_new", "label": "backend_host"})

    async with client_with(handle, seen) as client:
        await set_environment_variable(
            client, label="backend_host", environment="production", value="abc.trycloudflare.com"
        )

    [create] = [request for request in seen if request.method == "POST"]
    assert request_body(create) == {
        "type": "string",
        "label": "backend_host",
        "values": {"production": "abc.trycloudflare.com"},
    }
    assert not any(request.method == "PATCH" for request in seen)


async def test_an_existing_label_is_updated_for_the_named_environment_only() -> None:
    """Setting development must leave production alone, because production serves real calls."""
    seen: list[httpx.Request] = []
    existing = {
        "id": "env_1",
        "label": "backend_host",
        "type": "string",
        "values": {"production": "prod.example.com"},
    }

    def handle(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return variable_listing(existing)
        return httpx.Response(200, json=existing)

    async with client_with(handle, seen) as client:
        await set_environment_variable(
            client, label="backend_host", environment="development", value="abc.trycloudflare.com"
        )

    [update] = [request for request in seen if request.method == "PATCH"]
    assert update.url.path == f"{ENVIRONMENT_VARIABLES}/env_1"
    assert request_body(update) == {"values": {"development": "abc.trycloudflare.com"}}
    assert not any(request.method == "POST" for request in seen)


async def test_a_missing_label_outside_production_is_refused_before_any_write() -> None:
    """Creating a variable needs a production value, so the refusal must say to start there."""
    seen: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        return variable_listing()

    async with client_with(handle, seen) as client:
        with pytest.raises(ValueError, match="production"):
            await set_environment_variable(
                client,
                label="backend_host",
                environment="development",
                value="abc.trycloudflare.com",
            )

    assert not any(request.method in {"POST", "PATCH"} for request in seen)


@pytest.mark.parametrize("host", ["abc.trycloudflare.com", "localhost:8000"])
def test_a_bare_hostname_with_or_without_a_port_is_accepted(host: str) -> None:
    """The hostname is returned as typed. Nothing is rewritten to make it acceptable."""
    assert validate_backend_host(host) == host


@pytest.mark.parametrize(
    ("host", "reason"),
    [
        ("https://abc.com", "scheme"),
        ("abc.com/tools", "path"),
        ("ab c.com", "whitespace"),
        ("", "empty"),
        ("abc..com", "bare hostname"),
        ("abc.com:99999", "port"),
    ],
)
def test_a_host_that_is_not_bare_is_refused_with_the_reason(host: str, reason: str) -> None:
    """Each refusal names its own problem. The scheme case is pinned by its message, not just by
    being refused: the general hostname pattern would also refuse "https://abc.com", so only the
    message shows that the scheme check exists and runs first."""
    with pytest.raises(ValueError, match=reason):
        validate_backend_host(host)
