"""Setting up the ElevenLabs workspace from the repo's files, and writing results into `.env`.

The platform is a stateful fake behind `httpx.MockTransport`, not a list of canned replies, because
the property that matters is idempotency: running setup twice must find what the first run made
and update it, not make a second copy. Nothing here reaches the network.
"""

import importlib.util
import json
from collections.abc import Awaitable, Callable
from pathlib import Path
from types import ModuleType
from typing import Any

import httpx
import pytest

from maitre_d.elevenlabs import (
    ElevenLabsError,
    create_webhook,
    delete_webhooks_named,
    ensure_secret,
    ensure_secret_environment_variable,
    make_client,
    upsert_agent,
    upsert_tool,
    use_for_post_call_transcripts,
)
from maitre_d.envfile import set_env_value

API_KEY = "xi-test-key-not-a-real-key"
BEARER = "bearer-value-for-tests"


def load_script(name: str) -> ModuleType:
    path = Path(__file__).resolve().parents[1] / "scripts" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


setup = load_script("setup_elevenlabs")
runner = load_script("run_agent_tests")


class FakePlatform:
    """The parts of the ElevenLabs API that setup touches, with state and exact-ish semantics.

    List endpoints match `search` by prefix, as the real ones do, so a test meets the same
    near-miss names the platform returns.
    """

    def __init__(self) -> None:
        self.secrets: dict[str, dict[str, str]] = {}
        self.variables: dict[str, dict[str, Any]] = {}
        self.documents: dict[str, tuple[str, str]] = {}
        self.tools: dict[str, dict[str, Any]] = {}
        self.agents: dict[str, dict[str, Any]] = {}
        self.webhooks: dict[str, dict[str, Any]] = {}
        self.settings: dict[str, Any] = {}
        self.tests: dict[str, dict[str, Any]] = {}
        self.runs: dict[str, list[str]] = {}
        self.polls_until_finished = 1
        self._polls: dict[str, int] = {}
        self.requests: list[tuple[str, str]] = []
        self.unknown_agent_status = 404
        self._counter = 0

    def next_id(self, prefix: str) -> str:
        self._counter += 1
        return f"{prefix}_{self._counter}"

    def writes(self) -> list[tuple[str, str]]:
        return [r for r in self.requests if r[0] in {"POST", "PATCH", "DELETE"}]

    def client(self) -> httpx.AsyncClient:
        return make_client(API_KEY, transport=httpx.MockTransport(self.handle))

    def handle(self, request: httpx.Request) -> httpx.Response:
        method, path = request.method, request.url.path
        self.requests.append((method, path))
        body = json.loads(request.content) if request.content else {}
        search = request.url.params.get("search", "")
        parts = path.strip("/").split("/")

        if path == "/v1/convai/secrets":
            if method == "GET":
                found = [
                    {"secret_id": i, "name": s["name"]}
                    for i, s in self.secrets.items()
                    if s["name"].startswith(search)
                ]
                return httpx.Response(200, json={"secrets": found, "next_cursor": None})
            secret_id = self.next_id("secret")
            self.secrets[secret_id] = {"name": body["name"], "value": body["value"]}
            return httpx.Response(
                200, json={"type": "stored", "secret_id": secret_id, "name": body["name"]}
            )
        if path.startswith("/v1/convai/secrets/") and method == "PATCH":
            self.secrets[parts[-1]].update(name=body["name"], value=body["value"])
            return httpx.Response(200, json={"secret_id": parts[-1]})

        if path == "/v1/convai/environment-variables":
            if method == "GET":
                label = request.url.params.get("label")
                found = [{"id": i, **v} for i, v in self.variables.items() if v["label"] == label]
                listing = {"environment_variables": found, "has_more": False, "next_cursor": None}
                return httpx.Response(200, json=listing)
            variable_id = self.next_id("var")
            self.variables[variable_id] = {
                "label": body["label"],
                "type": body["type"],
                "values": body["values"],
            }
            return httpx.Response(200, json={"id": variable_id})
        if path.startswith("/v1/convai/environment-variables/") and method == "PATCH":
            self.variables[parts[-1]]["values"].update(body["values"])
            return httpx.Response(200, json={"id": parts[-1]})

        if path == "/v1/convai/knowledge-base" and method == "GET":
            found = [
                {"id": i, "name": n, "type": "text"}
                for i, (n, _t) in self.documents.items()
                if n.startswith(search)
            ]
            return httpx.Response(
                200, json={"documents": found, "has_more": False, "next_cursor": None}
            )
        if path.endswith("/content") and method == "GET":
            return httpx.Response(200, text=self.documents[parts[-2]][1])
        if path == "/v1/convai/knowledge-base/text" and method == "POST":
            document_id = self.next_id("doc")
            self.documents[document_id] = (body["name"], body["text"])
            return httpx.Response(200, json={"id": document_id, "name": body["name"]})

        if path == "/v1/convai/tools":
            if method == "GET":
                listed_tools = [
                    {"id": i, "tool_config": c}
                    for i, c in self.tools.items()
                    if c["name"].startswith(search)
                ]
                return httpx.Response(
                    200, json={"tools": listed_tools, "has_more": False, "next_cursor": None}
                )
            tool_id = self.next_id("tool")
            self.tools[tool_id] = body["tool_config"]
            return httpx.Response(200, json={"id": tool_id, "tool_config": body["tool_config"]})
        if path.startswith("/v1/convai/tools/") and method == "PATCH":
            self.tools[parts[-1]] = body["tool_config"]
            return httpx.Response(200, json={"id": parts[-1]})

        if path == "/v1/convai/agent-testing" and method == "GET":
            listed_tests = [
                {"id": i, "name": c["name"]}
                for i, c in self.tests.items()
                if c["name"].startswith(search)
            ]
            return httpx.Response(
                200, json={"tests": listed_tests, "has_more": False, "next_cursor": None}
            )
        if path == "/v1/convai/agent-testing/create" and method == "POST":
            test_id = self.next_id("test")
            self.tests[test_id] = body
            return httpx.Response(200, json={"id": test_id})
        if path.startswith("/v1/convai/agent-testing/") and method == "PUT":
            self.tests[parts[-1]] = body
            return httpx.Response(200, json={"id": parts[-1]})
        if path.endswith("/run-tests") and method == "POST":
            invocation_id = self.next_id("inv")
            self.runs[invocation_id] = [t["test_id"] for t in body["tests"]]
            return httpx.Response(200, json={"id": invocation_id})
        if path.startswith("/v1/convai/test-invocations/") and method == "GET":
            invocation_id = parts[-1]
            self._polls[invocation_id] = self._polls.get(invocation_id, 0) + 1
            pending = self._polls[invocation_id] <= self.polls_until_finished
            runs = [
                {
                    "test_name": self.tests[test_id]["name"],
                    "status": "pending" if pending else "passed",
                    "credits_used": None if pending else 3,
                }
                for test_id in self.runs[invocation_id]
            ]
            return httpx.Response(200, json={"id": invocation_id, "test_runs": runs})

        if path == "/v1/convai/agents/create" and method == "POST":
            agent_id = self.next_id("agent")
            self.agents[agent_id] = body
            return httpx.Response(200, json={"agent_id": agent_id})
        if path.startswith("/v1/convai/agents/"):
            agent_id = parts[-1]
            if method == "GET":
                if agent_id in self.agents:
                    return httpx.Response(200, json={"agent_id": agent_id})
                return httpx.Response(self.unknown_agent_status, json={"detail": "no such agent"})
            self.agents[agent_id] = body
            return httpx.Response(200, json={"agent_id": agent_id})

        if path == "/v1/workspace/webhooks":
            if method == "GET":
                found = [{"webhook_id": i, "name": w["name"]} for i, w in self.webhooks.items()]
                return httpx.Response(200, json={"webhooks": found})
            webhook_id = self.next_id("wh")
            self.webhooks[webhook_id] = body["settings"]
            return httpx.Response(
                200, json={"webhook_id": webhook_id, "webhook_secret": "whsec-fake-secret"}
            )
        if path.startswith("/v1/workspace/webhooks/"):
            if method == "DELETE":
                del self.webhooks[parts[-1]]
            else:
                self.webhooks[parts[-1]].update(body)
            return httpx.Response(200, json={})
        if path == "/v1/convai/settings" and method == "PATCH":
            self.settings.update(body)
            return httpx.Response(200, json={})
        return httpx.Response(404, json={"detail": f"unexpected {method} {path}"})


@pytest.fixture
def platform() -> FakePlatform:
    return FakePlatform()


def provision_with(
    platform: FakePlatform, *, bearer: str = BEARER, agent_id: str | None = None
) -> Callable[[], Awaitable[str]]:
    agent_config = json.loads(
        (setup.AGENT_DIR / "agent_configs" / "saffron_table.json").read_text(encoding="utf-8")
    )
    documents = {
        name: (setup.KNOWLEDGE_DIR / name).read_text(encoding="utf-8")
        for name in setup.DOCUMENT_NAMES
    }

    async def run() -> str:
        async with platform.client() as client:
            result: str = await setup.provision(
                client,
                bearer=bearer,
                agent_id=agent_id,
                agent_config=agent_config,
                tool_configs=setup.load_tool_configs(),
                documents=documents,
                test_configs=setup.load_test_configs(),
            )
            return result

    return run


async def test_setup_creates_everything_the_agent_needs(platform: FakePlatform) -> None:
    agent_id = await provision_with(platform)()

    assert [s["name"] for s in platform.secrets.values()] == ["tool_bearer"]
    assert {v["label"] for v in platform.variables.values()} == {"tool_bearer", "backend_host"}
    assert sorted(name for name, _ in platform.documents.values()) == ["menu.md", "restaurant.md"]
    assert sorted(c["name"] for c in platform.tools.values()) == [
        "cancel_booking",
        "change_booking",
        "check_availability",
        "create_booking",
        "find_booking",
        "take_message",
    ]
    assert list(platform.agents) == [agent_id]


async def test_the_agent_is_sent_with_this_workspaces_tool_and_document_ids(
    platform: FakePlatform,
) -> None:
    agent_id = await provision_with(platform)()

    prompt = platform.agents[agent_id]["conversation_config"]["agent"]["prompt"]
    assert sorted(prompt["tool_ids"]) == sorted(platform.tools)
    assert {d["id"] for d in prompt["knowledge_base"]} == set(platform.documents)
    assert {d["usage_mode"] for d in prompt["knowledge_base"]} == {"prompt"}


async def test_running_setup_a_second_time_creates_nothing_and_changes_no_id(
    platform: FakePlatform,
) -> None:
    """The whole point of setup being safe to repeat: it finds what it made and updates it."""
    agent_id = await provision_with(platform)()
    tools, documents, secrets = set(platform.tools), set(platform.documents), set(platform.secrets)
    platform.requests.clear()

    second = await provision_with(platform, agent_id=agent_id)()

    assert second == agent_id
    assert (set(platform.tools), set(platform.documents), set(platform.secrets)) == (
        tools,
        documents,
        secrets,
    )
    assert len(platform.variables) == 2
    assert not [r for r in platform.writes() if r[0] == "POST"], "a second run must not create"


async def test_the_secret_holds_the_bearer_prefixed_value_and_is_rotated_in_place(
    platform: FakePlatform,
) -> None:
    """The header is the secret's whole value, so it has to include the scheme.

    Changing TOOL_BEARER_SECRET and running setup again updates the same secret.
    """
    agent_id = await provision_with(platform, bearer="first")()
    [secret_id] = platform.secrets
    assert platform.secrets[secret_id]["value"] == "Bearer first"

    await provision_with(platform, bearer="second", agent_id=agent_id)()

    assert list(platform.secrets) == [secret_id]
    assert platform.secrets[secret_id]["value"] == "Bearer second"


async def test_a_backend_host_that_is_already_set_is_never_overwritten(
    platform: FakePlatform,
) -> None:
    """Setup runs often; the tunnel's hostname is set by another script and must survive it."""
    platform.variables["var_existing"] = {
        "label": "backend_host",
        "type": "string",
        "values": {"production": "abc.trycloudflare.com"},
    }

    await provision_with(platform)()

    assert platform.variables["var_existing"]["values"] == {"production": "abc.trycloudflare.com"}


async def test_a_new_backend_host_starts_as_a_name_that_can_never_resolve(
    platform: FakePlatform,
) -> None:
    await provision_with(platform)()

    host = next(v for v in platform.variables.values() if v["label"] == "backend_host")
    assert host["values"] == {"production": "tunnel-not-started.invalid"}
    assert host["values"]["production"].endswith(".invalid")


async def test_the_environment_variable_points_at_the_secret_by_id(
    platform: FakePlatform,
) -> None:
    await provision_with(platform)()

    [secret_id] = platform.secrets
    bearer = next(v for v in platform.variables.values() if v["label"] == "tool_bearer")
    assert bearer["type"] == "secret"
    assert bearer["values"] == {"production": {"secret_id": secret_id}}


async def test_a_similarly_named_secret_or_tool_is_not_mistaken_for_ours(
    platform: FakePlatform,
) -> None:
    """The platform's search is a prefix match, so near-misses come back and must be ignored."""
    platform.secrets["secret_other"] = {"name": "tool_bearer_old", "value": "keep me"}
    platform.tools["tool_other"] = {"name": "check_availability_v2", "type": "webhook"}

    async with platform.client() as client:
        secret_id = await ensure_secret(client, name="tool_bearer", value="Bearer x")
        tool_id = await upsert_tool(client, config={"name": "check_availability"})

    assert secret_id != "secret_other"
    assert tool_id != "tool_other"
    assert platform.secrets["secret_other"]["value"] == "keep me"
    assert platform.tools["tool_other"]["name"] == "check_availability_v2"


async def test_a_tool_config_without_a_name_is_refused_before_any_request(
    platform: FakePlatform,
) -> None:
    async with platform.client() as client:
        with pytest.raises(ValueError, match="name"):
            await upsert_tool(client, config={"description": "nameless"})

    assert platform.requests == []


async def test_a_secret_variable_is_updated_in_place_when_the_label_exists(
    platform: FakePlatform,
) -> None:
    async with platform.client() as client:
        await ensure_secret_environment_variable(client, label="tool_bearer", secret_id="s1")
        await ensure_secret_environment_variable(client, label="tool_bearer", secret_id="s2")

    [variable] = platform.variables.values()
    assert variable["values"] == {"production": {"secret_id": "s2"}}


@pytest.mark.parametrize("status", [404, 422])
async def test_an_agent_id_the_platform_does_not_know_means_create_a_new_agent(
    platform: FakePlatform, status: int
) -> None:
    """A stale or placeholder ID in `.env` must not stop setup from working on a fresh account."""
    platform.unknown_agent_status = status

    async with platform.client() as client:
        agent_id = await upsert_agent(client, agent_id="agent_from_an_old_account", config={})

    assert agent_id in platform.agents
    assert agent_id != "agent_from_an_old_account"


async def test_a_server_error_while_looking_for_the_agent_is_not_taken_for_no_agent(
    platform: FakePlatform,
) -> None:
    """If the lookup itself failed we don't know whether the agent exists; creating a second
    one would leave a duplicate."""
    platform.unknown_agent_status = 500

    async with platform.client() as client:
        with pytest.raises(ElevenLabsError) as error:
            await upsert_agent(client, agent_id="agent_x", config={})

    assert error.value.status_code == 500
    assert platform.agents == {}


@pytest.mark.parametrize("agent_id", [None, ""])
async def test_with_no_agent_id_the_agent_is_created_without_a_lookup(
    platform: FakePlatform, agent_id: str | None
) -> None:
    async with platform.client() as client:
        await upsert_agent(client, agent_id=agent_id, config={})

    assert [r for r in platform.requests if r[0] == "GET"] == []


async def test_replacing_the_webhook_removes_only_ours_and_returns_a_working_secret(
    platform: FakePlatform,
) -> None:
    platform.webhooks["wh_old"] = {"name": "Saffron Table post-call"}
    platform.webhooks["wh_other"] = {"name": "Someone else's webhook"}

    async with platform.client() as client:
        removed = await delete_webhooks_named(client, "Saffron Table post-call")
        created = await create_webhook(
            client, name="Saffron Table post-call", url="https://x.example/webhooks/elevenlabs"
        )

    assert removed == 1
    assert set(platform.webhooks) == {"wh_other", created.webhook_id}
    assert created.secret == "whsec-fake-secret"
    assert platform.webhooks[created.webhook_id]["auth_type"] == "hmac"


async def test_a_webhooks_signing_secret_never_appears_in_its_repr(
    platform: FakePlatform,
) -> None:
    """The secret is shown by the platform once; a traceback or log line must not repeat it."""
    async with platform.client() as client:
        created = await create_webhook(client, name="n", url="https://x.example/webhooks")

    assert "whsec-fake-secret" not in repr(created)


async def test_post_call_transcripts_are_the_only_event_sent_and_retries_are_on(
    platform: FakePlatform,
) -> None:
    platform.webhooks["wh_1"] = {"name": "Saffron Table post-call"}

    async with platform.client() as client:
        await use_for_post_call_transcripts(
            client, webhook_id="wh_1", name="Saffron Table post-call"
        )

    assert platform.settings["webhooks"] == {
        "post_call_webhook_id": "wh_1",
        "events": ["transcript"],
    }
    assert platform.webhooks["wh_1"]["retry_enabled"] is True
    assert platform.webhooks["wh_1"]["is_disabled"] is False


def test_the_committed_agent_config_is_not_changed_by_building_the_payload() -> None:
    """The committed config keeps its ID lists empty; the IDs are per workspace."""
    config = json.loads(
        (setup.AGENT_DIR / "agent_configs" / "saffron_table.json").read_text(encoding="utf-8")
    )
    before = json.dumps(config, sort_keys=True)

    payload = setup.build_agent_payload(
        config, tool_ids=["t1"], document_ids={"menu.md": "d1", "restaurant.md": "d2"}
    )

    assert json.dumps(config, sort_keys=True) == before
    assert payload["conversation_config"]["agent"]["prompt"]["tool_ids"] == ["t1"]
    assert config["conversation_config"]["agent"]["prompt"]["tool_ids"] == []
    assert config["conversation_config"]["agent"]["prompt"]["knowledge_base"] == []


def test_every_tool_config_in_the_repo_is_loaded_in_name_order() -> None:
    assert list(setup.load_tool_configs()) == sorted(setup.load_tool_configs())
    assert len(setup.load_tool_configs()) == 6


# `.env` writing


def test_a_new_key_is_appended_and_an_existing_one_replaced_in_place(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    env.write_text("A=1\nELEVENLABS_AGENT_ID=old\nB=2\n", encoding="utf-8")

    set_env_value(env, "ELEVENLABS_AGENT_ID", "agent_new")
    set_env_value(env, "C", "3")

    assert env.read_text(encoding="utf-8") == "A=1\nELEVENLABS_AGENT_ID=agent_new\nB=2\nC=3\n"


def test_comments_and_blank_lines_are_left_alone_and_a_commented_key_is_not_a_match(
    tmp_path: Path,
) -> None:
    """`# KEY=old` is documentation, and rewriting it would leave the real setting unset."""
    env = tmp_path / ".env"
    env.write_text("# KEY=old\n\nOTHER=x\n", encoding="utf-8")

    set_env_value(env, "KEY", "new")

    assert env.read_text(encoding="utf-8") == "# KEY=old\n\nOTHER=x\nKEY=new\n"


def test_a_missing_env_file_is_created(tmp_path: Path) -> None:
    env = tmp_path / ".env"

    set_env_value(env, "KEY", "value")

    assert env.read_text(encoding="utf-8") == "KEY=value\n"


def test_a_value_with_a_newline_is_refused_so_it_cannot_inject_another_setting(
    tmp_path: Path,
) -> None:
    env = tmp_path / ".env"
    env.write_text("A=1\n", encoding="utf-8")

    with pytest.raises(ValueError, match="single line"):
        set_env_value(env, "KEY", "value\nTOOL_BEARER_SECRET=attacker")

    assert env.read_text(encoding="utf-8") == "A=1\n"


async def test_skipping_the_secret_creates_the_rest_and_touches_no_secret_or_bearer_variable(
    platform: FakePlatform,
) -> None:
    """For anyone who would rather create the secret by hand than let a script write it."""
    agent_config = json.loads(
        (setup.AGENT_DIR / "agent_configs" / "saffron_table.json").read_text(encoding="utf-8")
    )
    documents = {
        name: (setup.KNOWLEDGE_DIR / name).read_text(encoding="utf-8")
        for name in setup.DOCUMENT_NAMES
    }

    async with platform.client() as client:
        await setup.provision(
            client,
            bearer=BEARER,
            agent_id=None,
            agent_config=agent_config,
            tool_configs=setup.load_tool_configs(),
            documents=documents,
            manage_secret=False,
        )

    assert platform.secrets == {}
    assert {v["label"] for v in platform.variables.values()} == {"backend_host"}
    assert len(platform.tools) == 6 and len(platform.agents) == 1
    assert not [r for r in platform.requests if "secrets" in r[1]]


# Agent tests: provisioning, tool references, and the runner


async def test_setup_creates_each_agent_test_and_attaches_them_to_the_agent(
    platform: FakePlatform,
) -> None:
    agent_id = await provision_with(platform)()

    assert len(platform.tests) == 5
    attached = platform.agents[agent_id]["platform_settings"]["testing"]["attached_tests"]
    assert {a["test_id"] for a in attached} == set(platform.tests)


async def test_a_test_refers_to_this_workspaces_tool_ids_not_to_names(
    platform: FakePlatform,
) -> None:
    """The committed tests say `@check_availability`; the platform needs the real ID."""
    await provision_with(platform)()

    ids = {config["name"]: tool_id for tool_id, config in platform.tools.items()}
    book = next(c for c in platform.tests.values() if c["type"] == "tool")
    full = next(c for c in platform.tests.values() if c["name"].startswith("Offers the alt"))
    assert book["tool_call_parameters"]["referenced_tool"]["id"] == ids["check_availability"]
    assert full["tool_mock_config"]["mocked_tool_ids"] == [ids["check_availability"]]
    assert list(full["tool_mock_overrides"]) == [ids["check_availability"]]
    assert "@" not in json.dumps(list(platform.tests.values())), "an unresolved placeholder"


def test_every_at_reference_is_resolved_wherever_it_appears() -> None:
    ids = {"find_booking": "tool_9"}
    config = {
        "ids": ["@find_booking"],
        "nested": {"@find_booking": [{"deep": "@find_booking"}]},
        "plain": "write to me@example.com or ask @someone in the text",
    }

    resolved = setup.resolve_tool_refs(config, ids)

    assert resolved == {
        "ids": ["tool_9"],
        "nested": {"tool_9": [{"deep": "tool_9"}]},
        "plain": "write to me@example.com or ask @someone in the text",
    }


def test_a_reference_to_a_tool_that_does_not_exist_is_an_error_not_a_string_sent_on() -> None:
    with pytest.raises(ValueError, match="@check_availabilty"):
        setup.resolve_tool_refs({"id": "@check_availabilty"}, {"check_availability": "t1"})


async def test_a_test_is_found_by_its_exact_name_and_updated_in_place(
    platform: FakePlatform,
) -> None:
    from maitre_d.elevenlabs import upsert_test

    platform.tests["test_other"] = {"name": "Books a table for four on Friday"}

    async with platform.client() as client:
        first = await upsert_test(client, config={"name": "Books a table", "type": "llm"})
        second = await upsert_test(
            client, config={"name": "Books a table", "type": "llm", "success_condition": "x"}
        )

    assert first == second != "test_other"
    assert platform.tests[first]["success_condition"] == "x"
    assert platform.tests["test_other"] == {"name": "Books a table for four on Friday"}


def test_the_runner_adds_up_only_the_credits_the_platform_reported() -> None:
    rows, finished = runner.summarise(
        {
            "test_runs": [
                {"test_name": "a", "status": "passed", "credits_used": 4},
                {"test_name": "b", "status": "failed", "credits_used": None},
                "not a run",
            ]
        }
    )

    assert finished is True
    assert rows == [("a", "passed", 4), ("b", "failed", None)]


def test_the_runner_keeps_waiting_while_any_one_test_is_still_running() -> None:
    """Stopping at the first finished test would report a total that leaves out the rest."""
    _, finished = runner.summarise(
        {
            "test_runs": [
                {"test_name": "a", "status": "passed", "credits_used": 2},
                {"test_name": "b", "status": "pending"},
            ]
        }
    )

    assert finished is False


def test_a_run_with_a_pending_test_is_not_finished() -> None:
    _, finished = runner.summarise({"test_runs": [{"test_name": "a", "status": "pending"}]})

    assert finished is False
    assert runner.summarise({}) == ([], False), "no runs yet is not finished"


async def test_the_runner_waits_for_pending_tests_then_reports_their_credits(
    platform: FakePlatform, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(runner, "POLL_SECONDS", 0)
    platform.polls_until_finished = 3
    platform.tests["test_1"] = {"name": "Books a table"}

    async with platform.client() as client:
        rows = await runner.run(client, "agent_1", ["Books a table"])

    assert rows == [("Books a table", "passed", 3)]
    assert [r for r in platform.requests if "test-invocations" in r[1]].__len__() == 4


async def test_the_runner_gives_up_instead_of_polling_forever(
    platform: FakePlatform, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(runner, "POLL_SECONDS", 0)
    monkeypatch.setattr(runner, "GIVE_UP_AFTER_SECONDS", 0)
    platform.polls_until_finished = 10**6
    platform.tests["test_1"] = {"name": "Books a table"}

    async with platform.client() as client:
        with pytest.raises(SystemExit, match="Gave up"):
            await runner.run(client, "agent_1", ["Books a table"])


async def test_the_runner_refuses_a_test_that_is_not_on_the_platform_before_spending_anything(
    platform: FakePlatform,
) -> None:
    async with platform.client() as client:
        with pytest.raises(SystemExit, match="not on the platform"):
            await runner.run(client, "agent_1", ["Never created"])

    assert platform.runs == {}


def test_running_a_test_needs_an_explicit_confirmation_of_the_spend(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Nothing reaches the network: asking to run without the flag stops at the argument check."""
    monkeypatch.setattr("sys.argv", ["run_agent_tests.py", "book_next_friday"])

    with pytest.raises(SystemExit, match="--confirm-spend"):
        runner.main()


def test_listing_the_tests_runs_nothing_and_needs_no_credentials(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr("sys.argv", ["run_agent_tests.py"])
    monkeypatch.setattr(runner, "make_client", lambda *a, **k: pytest.fail("made a client"))

    runner.main()

    assert "book_next_friday" in capsys.readouterr().out


def test_an_unknown_test_name_is_refused_with_the_names_that_exist(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("sys.argv", ["run_agent_tests.py", "nope", "--confirm-spend"])

    with pytest.raises(SystemExit, match="book_next_friday"):
        runner.main()
