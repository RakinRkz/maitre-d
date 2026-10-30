"""Outbound calls to the ElevenLabs REST API, shared by the backend and the setup scripts.

This is plain httpx rather than the `elevenlabs` SDK (docs/decisions.md #11): each call is one
request with an `xi-api-key` header. Every function takes the client as an argument, so tests can
pass in an `httpx.MockTransport` and the session route can reuse the app's client.
"""

import re
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import quote

import httpx

BASE_URL = "https://api.elevenlabs.io"
# Long enough for a slow network, short enough that a dead connection doesn't hold a caller open.
TIMEOUT_SECONDS = 10.0
PRODUCTION = "production"

_CONVAI = "/v1/convai"
_KNOWLEDGE_BASE = f"{_CONVAI}/knowledge-base"
_ENVIRONMENT_VARIABLES = f"{_CONVAI}/environment-variables"
# Platform error text is shown to people in a terminal or a log. A long body is more likely to be
# an HTML page or a dump than a useful sentence, so the text is cut short.
_MAX_DETAIL_CHARS = 300


class ElevenLabsError(Exception):
    """A non-success answer from ElevenLabs, or a success whose body is not the shape we expect.

    `str()` holds the status and the platform's own error text. It never holds the request,
    because the request headers carry the API key.
    """

    def __init__(self, status_code: int, detail: str) -> None:
        self.status_code = status_code
        self.detail = detail
        super().__init__(f"HTTP {status_code}: {detail}" if detail else f"HTTP {status_code}")


@dataclass(frozen=True)
class ConversationToken:
    """A WebRTC session token for one conversation.

    The token is short-lived but still a credential, so `repr` leaves it out. A traceback or a
    log line that prints this object should not put a usable token in a log.
    """

    token: str = field(repr=False)
    conversation_id: str


@dataclass(frozen=True)
class CreatedWebhook:
    """A new workspace webhook. The signing secret is shown by the platform only once."""

    webhook_id: str
    secret: str = field(repr=False)


# RFC 1123 host labels: letters, digits and inner hyphens, at most 63 characters each.
_LABEL = r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?"
_BARE_HOST = re.compile(rf"{_LABEL}(?:\.{_LABEL})*(?::[0-9]{{1,5}})?")
MAX_PORT = 65535


def validate_backend_host(host: str) -> str:
    """Return `host` unchanged if it is a bare hostname, with an optional port. Otherwise raise.

    The checks run in order, so each refusal gives the reason for its own case. Removing a
    scheme or a trailing slash would look helpful, but then the variable would hold something
    other than what the operator typed. Refusing lets them see the mistake and fix it.
    """
    if not host:
        raise ValueError("the backend host must not be empty")
    if any(character.isspace() for character in host):
        raise ValueError(f"the backend host must not contain whitespace: {host!r}")
    if "://" in host:
        raise ValueError("give the hostname without the scheme, for example abc.trycloudflare.com")
    if "/" in host:
        raise ValueError("give the hostname without a path, for example abc.trycloudflare.com")
    if not _BARE_HOST.fullmatch(host):
        raise ValueError(f"expected a bare hostname, optionally with a port: {host!r}")
    port = host.partition(":")[2]
    if port and int(port) > MAX_PORT:
        raise ValueError(f"the port must be at most {MAX_PORT}: {host!r}")
    return host


def make_client(
    api_key: str,
    *,
    base_url: str = BASE_URL,
    transport: httpx.AsyncBaseTransport | None = None,
) -> httpx.AsyncClient:
    """An async client that sends the key on every request.

    The key is a default header, so every call made through this client is authenticated and no
    call site can forget it.
    """
    return httpx.AsyncClient(
        base_url=base_url,
        headers={"xi-api-key": api_key},
        timeout=TIMEOUT_SECONDS,
        transport=transport,
    )


async def get_conversation_token(
    client: httpx.AsyncClient, *, agent_id: str, environment: str | None = None
) -> ConversationToken:
    params: dict[str, Any] = {"agent_id": agent_id}
    if environment is not None:
        # Left out when unset, so the platform applies its own default rather than ours.
        params["environment"] = environment
    response = await _request(client, "GET", f"{_CONVAI}/conversation/token", params=params)
    body = _json_object(response)
    return ConversationToken(
        token=_string_field(response, body, "token"),
        conversation_id=_string_field(response, body, "conversation_id"),
    )


async def ensure_text_document(client: httpx.AsyncClient, *, name: str, text: str) -> str:
    """The id of a knowledge-base text document with this name and content.

    See `ensure_text_document_outcome` for what is reused and what is created.
    """
    document_id, _created = await ensure_text_document_outcome(client, name=name, text=text)
    return document_id


async def ensure_text_document_outcome(
    client: httpx.AsyncClient, *, name: str, text: str
) -> tuple[str, bool]:
    """Return `(document_id, created)` for a knowledge-base text document with this name and text.

    An existing document is reused only when its name and content both match exactly. A document
    with the same name and different content is left alone, and a new one is created beside it.
    Nothing is deleted: an agent may still point at the old document, and removing it would break
    that agent. Old copies stay in the workspace until someone removes them in the dashboard.
    """
    for document_id in await _text_document_ids_named(client, name):
        if await _document_text(client, document_id) == text:
            return document_id, False
    response = await _request(
        client, "POST", f"{_KNOWLEDGE_BASE}/text", payload={"text": text, "name": name}
    )
    return _string_field(response, _json_object(response), "id"), True


async def set_environment_variable(
    client: httpx.AsyncClient, *, label: str, environment: str, value: str
) -> None:
    """Set one environment's value for the workspace variable `label`, creating it if needed.

    An existing variable is updated for `environment` only, so the other environments keep their
    values. A new variable must start with a production value, because the platform requires one.
    """
    variable_id = await _environment_variable_id(client, label)
    if variable_id is None:
        if environment != PRODUCTION:
            raise ValueError(
                f"there is no environment variable {label!r} yet. ElevenLabs needs a production "
                f"value to create one, so set it with environment {PRODUCTION!r} first."
            )
        await _request(
            client,
            "POST",
            _ENVIRONMENT_VARIABLES,
            payload={"type": "string", "label": label, "values": {PRODUCTION: value}},
        )
        return
    await _request(
        client,
        "PATCH",
        f"{_ENVIRONMENT_VARIABLES}/{quote(variable_id, safe='')}",
        payload={"values": {environment: value}},
    )


async def ensure_secret(client: httpx.AsyncClient, *, name: str, value: str) -> str:
    """The ID of the workspace secret called `name`, now holding `value`.

    An existing secret is updated on every run, so rotating the value and running setup again is
    enough to change what the platform sends.
    """
    secret_id = await _secret_id(client, name)
    if secret_id is None:
        response = await _request(
            client,
            "POST",
            f"{_CONVAI}/secrets",
            payload={"type": "new", "name": name, "value": value},
        )
        return _string_field(response, _json_object(response), "secret_id")
    await _request(
        client,
        "PATCH",
        f"{_CONVAI}/secrets/{quote(secret_id, safe='')}",
        payload={"type": "update", "name": name, "value": value},
    )
    return secret_id


async def ensure_secret_environment_variable(
    client: httpx.AsyncClient, *, label: str, secret_id: str
) -> None:
    """Make the environment variable `label` a reference to `secret_id`, in production.

    A tool header can refer to the variable by label, so the committed tool configs name no secret
    ID, and the secret's value never appears in a file.
    """
    reference = {"secret_id": secret_id}
    variable_id = await _environment_variable_id(client, label)
    if variable_id is None:
        await _request(
            client,
            "POST",
            _ENVIRONMENT_VARIABLES,
            payload={"type": "secret", "label": label, "values": {PRODUCTION: reference}},
        )
        return
    await _request(
        client,
        "PATCH",
        f"{_ENVIRONMENT_VARIABLES}/{quote(variable_id, safe='')}",
        payload={"values": {PRODUCTION: reference}},
    )


async def environment_variable_exists(client: httpx.AsyncClient, label: str) -> bool:
    return await _environment_variable_id(client, label) is not None


async def upsert_tool(client: httpx.AsyncClient, *, config: dict[str, Any]) -> str:
    """Create the tool described by `config`, or update the one of that name. Returns its ID."""
    name = config.get("name")
    if not isinstance(name, str) or not name:
        raise ValueError("a tool config needs a name")
    tool_id = await _tool_id(client, name)
    if tool_id is None:
        response = await _request(
            client, "POST", f"{_CONVAI}/tools", payload={"tool_config": config}
        )
        return _string_field(response, _json_object(response), "id")
    await _request(
        client,
        "PATCH",
        f"{_CONVAI}/tools/{quote(tool_id, safe='')}",
        payload={"tool_config": config},
    )
    return tool_id


async def upsert_agent(
    client: httpx.AsyncClient, *, agent_id: str | None, config: dict[str, Any]
) -> str:
    """Update the agent `agent_id` with `config`, or create one if there is no such agent.

    A configured ID that the platform doesn't know (a stale or placeholder value in `.env`) is
    treated as no agent, so a fresh account and a changed one take the same path.
    """
    if agent_id and await _agent_exists(client, agent_id):
        await _request(
            client, "PATCH", f"{_CONVAI}/agents/{quote(agent_id, safe='')}", payload=config
        )
        return agent_id
    response = await _request(client, "POST", f"{_CONVAI}/agents/create", payload=config)
    return _string_field(response, _json_object(response), "agent_id")


async def upsert_test(client: httpx.AsyncClient, *, config: dict[str, Any]) -> str:
    """Create the agent test in `config`, or update the one of that name; return its ID."""
    name = config.get("name")
    if not isinstance(name, str) or not name:
        raise ValueError("a test config needs a name")
    test_id = await find_test_id(client, name)
    if test_id is None:
        response = await _request(client, "POST", f"{_CONVAI}/agent-testing/create", payload=config)
        return _string_field(response, _json_object(response), "id")
    await _request(
        client, "PUT", f"{_CONVAI}/agent-testing/{quote(test_id, safe='')}", payload=config
    )
    return test_id


async def find_test_id(client: httpx.AsyncClient, name: str) -> str | None:
    """The ID of the agent test named exactly `name`. The platform's search is a prefix match."""
    cursor: str | None = None
    while True:
        params: dict[str, Any] = {"search": name, "page_size": 100}
        if cursor is not None:
            params["cursor"] = cursor
        response = await _request(client, "GET", f"{_CONVAI}/agent-testing", params=params)
        body = _json_object(response)
        tests = body.get("tests")
        if not isinstance(tests, list):
            raise _unexpected(response, "no list field 'tests'")
        for test in tests:
            if isinstance(test, dict) and test.get("name") == name:
                test_id = test.get("id")
                if isinstance(test_id, str):
                    return test_id
        cursor = body.get("next_cursor")
        if not body.get("has_more") or not isinstance(cursor, str):
            return None


async def start_test_run(
    client: httpx.AsyncClient, *, agent_id: str, test_ids: list[str], name: str
) -> str:
    """Start the tests against the agent and return the invocation ID. This spends credits."""
    response = await _request(
        client,
        "POST",
        f"{_CONVAI}/agents/{quote(agent_id, safe='')}/run-tests",
        payload={"name": name, "tests": [{"test_id": test_id} for test_id in test_ids]},
    )
    return _string_field(response, _json_object(response), "id")


async def get_test_invocation(client: httpx.AsyncClient, invocation_id: str) -> dict[str, Any]:
    response = await _request(
        client, "GET", f"{_CONVAI}/test-invocations/{quote(invocation_id, safe='')}"
    )
    return _json_object(response)


async def delete_webhooks_named(client: httpx.AsyncClient, name: str) -> int:
    """Delete every workspace webhook called `name` and return how many there were.

    A webhook's URL can't be edited, and a quick tunnel gets a new hostname each time it starts,
    so the way to move it is to replace it.
    """
    response = await _request(client, "GET", "/v1/workspace/webhooks")
    webhooks = _json_object(response).get("webhooks")
    if not isinstance(webhooks, list):
        raise _unexpected(response, "no list field 'webhooks'")
    deleted = 0
    for webhook in webhooks:
        if isinstance(webhook, dict) and webhook.get("name") == name:
            webhook_id = webhook.get("webhook_id")
            if isinstance(webhook_id, str):
                await _request(
                    client, "DELETE", f"/v1/workspace/webhooks/{quote(webhook_id, safe='')}"
                )
                deleted += 1
    return deleted


async def create_webhook(client: httpx.AsyncClient, *, name: str, url: str) -> CreatedWebhook:
    """Create an HMAC-signed workspace webhook. Its secret is returned once and never again."""
    response = await _request(
        client,
        "POST",
        "/v1/workspace/webhooks",
        payload={"settings": {"auth_type": "hmac", "name": name, "webhook_url": url}},
    )
    body = _json_object(response)
    return CreatedWebhook(
        webhook_id=_string_field(response, body, "webhook_id"),
        secret=_string_field(response, body, "webhook_secret"),
    )


async def use_for_post_call_transcripts(
    client: httpx.AsyncClient, *, webhook_id: str, name: str
) -> None:
    """Send finished-call transcripts, and only those, to the webhook; retry failed deliveries."""
    await _request(
        client,
        "PATCH",
        f"{_CONVAI}/settings",
        payload={"webhooks": {"post_call_webhook_id": webhook_id, "events": ["transcript"]}},
    )
    await _request(
        client,
        "PATCH",
        f"/v1/workspace/webhooks/{quote(webhook_id, safe='')}",
        payload={"name": name, "is_disabled": False, "retry_enabled": True},
    )


async def _request(
    client: httpx.AsyncClient,
    method: str,
    path: str,
    *,
    params: dict[str, Any] | None = None,
    payload: dict[str, Any] | None = None,
) -> httpx.Response:
    response = await client.request(method, path, params=params, json=payload)
    if not response.is_success:
        raise _error_for(response, client.headers.get("xi-api-key", ""))
    return response


def _error_for(response: httpx.Response, api_key: str) -> ElevenLabsError:
    detail = _platform_message(response)
    if api_key:
        # Some error bodies echo the request back. The key must not reach a log line this way.
        detail = detail.replace(api_key, "[redacted]")
    return ElevenLabsError(response.status_code, detail[:_MAX_DETAIL_CHARS])


def _platform_message(response: httpx.Response) -> str:
    """The error text the platform gives, or "" when the body is not one of the shapes it uses.

    Only the message fields are read. The body is never passed on whole, because an unknown shape
    could hold anything.
    """
    try:
        body: Any = response.json()
    except ValueError:
        return ""
    if not isinstance(body, dict):
        return ""
    detail: Any = body.get("detail", body.get("message"))
    if isinstance(detail, str):
        return detail
    if isinstance(detail, dict):
        parts = [detail.get("status"), detail.get("message")]
        return ": ".join(part for part in parts if isinstance(part, str) and part)
    if isinstance(detail, list):
        # FastAPI-style validation errors: a list of {loc, msg, type} objects.
        messages = [item.get("msg") for item in detail if isinstance(item, dict)]
        return "; ".join(message for message in messages if isinstance(message, str))
    return ""


def _json_object(response: httpx.Response) -> dict[str, Any]:
    try:
        body: Any = response.json()
    except ValueError:
        body = None
    if not isinstance(body, dict):
        raise _unexpected(response, "the body is not a JSON object")
    return body


def _string_field(response: httpx.Response, body: dict[str, Any], name: str) -> str:
    value = body.get(name)
    if not isinstance(value, str):
        raise _unexpected(response, f"no string field {name!r}")
    return value


def _unexpected(response: httpx.Response, problem: str) -> ElevenLabsError:
    return ElevenLabsError(response.status_code, f"unexpected response, {problem}")


async def _secret_id(client: httpx.AsyncClient, name: str) -> str | None:
    cursor: str | None = None
    while True:
        params: dict[str, Any] = {"search": name, "page_size": 100}
        if cursor is not None:
            params["cursor"] = cursor
        response = await _request(client, "GET", f"{_CONVAI}/secrets", params=params)
        body = _json_object(response)
        secrets = body.get("secrets")
        if not isinstance(secrets, list):
            raise _unexpected(response, "no list field 'secrets'")
        for secret in secrets:
            if isinstance(secret, dict) and secret.get("name") == name:
                secret_id = secret.get("secret_id")
                if isinstance(secret_id, str):
                    return secret_id
        cursor = body.get("next_cursor")
        if not isinstance(cursor, str):
            return None


async def _tool_id(client: httpx.AsyncClient, name: str) -> str | None:
    """The ID of the tool whose name is exactly `name`. The platform's search is a prefix match."""
    cursor: str | None = None
    while True:
        params: dict[str, Any] = {"search": name, "page_size": 100}
        if cursor is not None:
            params["cursor"] = cursor
        response = await _request(client, "GET", f"{_CONVAI}/tools", params=params)
        body = _json_object(response)
        tools = body.get("tools")
        if not isinstance(tools, list):
            raise _unexpected(response, "no list field 'tools'")
        for tool in tools:
            config = tool.get("tool_config") if isinstance(tool, dict) else None
            if isinstance(config, dict) and config.get("name") == name:
                tool_id = tool.get("id")
                if isinstance(tool_id, str):
                    return tool_id
        cursor = body.get("next_cursor")
        if not body.get("has_more") or not isinstance(cursor, str):
            return None


async def _agent_exists(client: httpx.AsyncClient, agent_id: str) -> bool:
    response = await client.get(f"{_CONVAI}/agents/{quote(agent_id, safe='')}")
    if response.status_code in (404, 422):
        return False
    if not response.is_success:
        raise _error_for(response, client.headers.get("xi-api-key", ""))
    return True


async def _text_document_ids_named(client: httpx.AsyncClient, name: str) -> list[str]:
    """The ids of every text document whose name is exactly `name`.

    The platform's `search` matches by prefix, so "menu.md" also returns "menu.md.bak". The exact
    comparison is made here, because a prefix match alone would reuse the wrong document.
    """
    ids: list[str] = []
    cursor: str | None = None
    while True:
        params: dict[str, Any] = {"search": name, "types": ["text"], "page_size": 100}
        if cursor is not None:
            params["cursor"] = cursor
        response = await _request(client, "GET", _KNOWLEDGE_BASE, params=params)
        body = _json_object(response)
        documents = body.get("documents")
        if not isinstance(documents, list):
            raise _unexpected(response, "no list field 'documents'")
        for document in documents:
            if (
                isinstance(document, dict)
                and document.get("type") == "text"
                and document.get("name") == name
            ):
                document_id = document.get("id")
                if isinstance(document_id, str):
                    ids.append(document_id)
        cursor = body.get("next_cursor")
        if not body.get("has_more") or not isinstance(cursor, str):
            return ids


async def _document_text(client: httpx.AsyncClient, document_id: str) -> str:
    response = await _request(
        client, "GET", f"{_KNOWLEDGE_BASE}/{quote(document_id, safe='')}/content"
    )
    # The endpoint streams the document as text, not JSON. It is compared exactly as returned.
    return response.text


async def _environment_variable_id(client: httpx.AsyncClient, label: str) -> str | None:
    # Labels are unique, so filtering by label returns at most one variable and needs no paging.
    response = await _request(client, "GET", _ENVIRONMENT_VARIABLES, params={"label": label})
    body = _json_object(response)
    variables = body.get("environment_variables")
    if not isinstance(variables, list):
        raise _unexpected(response, "no list field 'environment_variables'")
    for variable in variables:
        if not isinstance(variable, dict):
            continue
        variable_id = variable.get("id")
        if variable.get("label") == label and isinstance(variable_id, str):
            return variable_id
    return None
