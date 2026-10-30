"""The agent's config files and the backend they describe must not drift apart.

The agent is configured as code under `agent/`. These tests read those files and compare them with
the things they promise: the request models the tool routes parse, the facts that have one
definition in `restaurant.py`, and the safety settings that protect the account. They need no
database and make no call to ElevenLabs.
"""

import json
import re
from pathlib import Path
from typing import Any

import pytest
from pydantic import BaseModel

from maitre_d import restaurant, schemas
from maitre_d.config import Settings
from maitre_d.main import create_app

ROOT = Path(__file__).resolve().parent.parent
AGENT = ROOT / "agent"
TOOL_CONFIGS = AGENT / "tool_configs"
AGENT_CONFIG = AGENT / "agent_configs" / "saffron_table.json"
PROMPT_FILE = AGENT / "prompts" / "saffron_table.md"

REQUEST_MODELS: dict[str, type[BaseModel]] = {
    "check_availability": schemas.CheckAvailabilityRequest,
    "create_booking": schemas.CreateBookingRequest,
    "find_booking": schemas.FindBookingRequest,
    "change_booking": schemas.ChangeBookingRequest,
    "cancel_booking": schemas.CancelBookingRequest,
    "take_message": schemas.TakeMessageRequest,
}


def load(path: Path) -> dict[str, Any]:
    data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return data


def tool_config(name: str) -> dict[str, Any]:
    return load(TOOL_CONFIGS / f"{name}.json")


def body_schema(name: str) -> dict[str, Any]:
    schema: dict[str, Any] = tool_config(name)["api_schema"]["request_body_schema"]
    return schema


def agent_config() -> dict[str, Any]:
    return load(AGENT_CONFIG)


def prompt_section() -> dict[str, Any]:
    section: dict[str, Any] = agent_config()["conversation_config"]["agent"]["prompt"]
    return section


def json_type(prop: dict[str, Any]) -> str:
    """The JSON type of a property, looking through the `anyOf: [X, null]` of an optional field."""
    if "type" in prop:
        return str(prop["type"])
    options = [option for option in prop.get("anyOf", []) if option.get("type") != "null"]
    return str(options[0]["type"])


def enum_values(prop: dict[str, Any]) -> list[str] | None:
    if "enum" in prop:
        values: list[str] = prop["enum"]
        return values
    for option in prop.get("anyOf", []):
        if "enum" in option:
            values = option["enum"]
            return values
    return None


def served_tools() -> set[str]:
    """The POST routes under /tools, read from the OpenAPI document the app publishes.

    Not from `app.routes`: included routers are nested there, and the OpenAPI document is the
    flat list of what the backend really serves.
    """
    paths: dict[str, dict[str, Any]] = create_app(Settings(_env_file=None)).openapi()["paths"]
    return {
        path.removeprefix("/tools/")
        for path, operations in paths.items()
        if path.startswith("/tools/") and "post" in operations
    }


def test_every_tool_the_backend_serves_has_a_config_and_the_reverse() -> None:
    """A route with no config is a tool the agent can never use; a config with no route 404s."""
    configured = {path.stem for path in TOOL_CONFIGS.glob("*.json")}

    assert served_tools() == configured == set(REQUEST_MODELS)
    for name in configured:
        assert tool_config(name)["name"] == name


@pytest.mark.parametrize("name", sorted(REQUEST_MODELS))
def test_each_tool_posts_to_its_own_route_through_the_environment_host(name: str) -> None:
    """The host is a workspace variable so one committed config works for any tunnel or deploy."""
    api = tool_config(name)["api_schema"]

    assert api["url"] == f"https://{{{{system__env_backend_host}}}}/tools/{name}"
    assert api["method"] == "POST"
    assert api["content_type"] == "application/json"


@pytest.mark.parametrize("name", sorted(REQUEST_MODELS))
def test_each_tool_body_matches_the_model_the_route_parses(name: str) -> None:
    """Same fields, same required set, same types and the same weekday names as the backend.

    If a field is added to a request model and not to the config, the agent never sends it; if
    it is required and missing from the config, every call fails validation.
    """
    model = REQUEST_MODELS[name].model_json_schema()
    configured = body_schema(name)

    assert set(configured["properties"]) == set(model["properties"])
    assert set(configured["required"]) == set(model.get("required", []))
    for field, prop in configured["properties"].items():
        if "dynamic_variable" in prop:
            continue
        assert json_type(prop) == json_type(model["properties"][field]), field
        assert enum_values(prop) == enum_values(model["properties"][field]), field


@pytest.mark.parametrize("name", sorted(REQUEST_MODELS))
def test_the_conversation_id_comes_from_the_platform_and_everything_else_from_the_model(
    name: str,
) -> None:
    """The model must not be able to choose the value that scopes a booking to a call.

    The idempotency key is derived from the conversation ID (decisions #2). If the LLM filled it,
    a prompt injection could pick another call's ID. Every other field is the caller's words.
    """
    properties = body_schema(name)["properties"]

    assert properties["conversation_id"] == {
        "type": "string",
        "dynamic_variable": "system__conversation_id",
    }
    for field, prop in properties.items():
        if field == "conversation_id":
            continue
        assert prop.get("description"), f"{field} needs a description for the model to fill it"
        assert not {"dynamic_variable", "constant_value", "is_omitted"} & set(prop), field


def test_no_agent_file_holds_a_credential() -> None:
    """Auth is a reference to a secret environment variable; the secret is never in the repo."""
    for name in REQUEST_MODELS:
        headers = tool_config(name)["api_schema"]["request_headers"]
        assert headers == {"Authorization": {"env_var_label": "tool_bearer"}}

    for path in AGENT.rglob("*"):
        if path.is_file() and path.suffix in {".json", ".md"}:
            text = path.read_text(encoding="utf-8")
            assert "Bearer " not in text, path
            assert "xi-api-key" not in text.lower(), path


def test_the_prompt_in_the_config_is_the_markdown_prompt() -> None:
    """The prompt is edited as Markdown and embedded; a stale embed means the agent runs old text.

    Fix with `uv run python scripts/sync_agent_prompt.py`.
    """
    assert prompt_section()["prompt"] == PROMPT_FILE.read_text(encoding="utf-8").rstrip("\n")


def test_the_prompt_names_every_tool_the_agent_can_use() -> None:
    """A tool the prompt never mentions is one the model has no instruction for using."""
    prompt = prompt_section()["prompt"]

    for name in [*REQUEST_MODELS, "end_call"]:
        assert name in prompt, name


def test_the_prompt_does_not_restate_facts_that_live_in_restaurant_py() -> None:
    """Hours and the address have one definition; a copy in the prompt would outlive a change.

    The knowledge base page, generated from restaurant.py, is where the agent reads them.
    """
    prompt = prompt_section()["prompt"]
    facts = {restaurant.ADDRESS}
    for services in restaurant.SERVICES_BY_WEEKDAY.values():
        for service in services:
            facts |= {f"{service.opens:%H:%M}", f"{service.closes:%H:%M}"}

    for fact in facts:
        assert fact not in prompt, fact


def test_the_prompt_uses_the_date_variable_that_keeps_the_prompt_cacheable() -> None:
    """`system__time_date` changes once a day; `system__time` changes every minute (plan §2)."""
    prompt = prompt_section()["prompt"]

    assert "{{system__time_date}}" in prompt
    assert not re.search(r"\{\{\s*system__time\s*\}\}", prompt)
    assert prompt_section()["timezone"] == restaurant.TIMEZONE.key


def test_the_agent_is_private_and_a_forgotten_call_cannot_burn_the_month() -> None:
    """The Free plan has 15 voice minutes a month and no overage, so every limit is deliberate."""
    config = agent_config()
    platform = config["platform_settings"]

    assert platform["auth"]["enable_auth"] is True
    assert config["conversation_config"]["conversation"]["max_duration_seconds"] <= 300
    assert platform["call_limits"]["bursting_enabled"] is False
    assert platform["call_limits"]["agent_concurrency_limit"] == 1
    assert config["conversation_config"]["turn"]["silence_end_call_timeout"] > 0


def test_the_agent_can_hang_up_and_attaches_its_tools_by_id() -> None:
    """`end_call` is built in. The webhook tools are standalone and attached by the IDs the
    platform gave them, which are filled in once they exist."""
    prompt = prompt_section()

    assert prompt["built_in_tools"]["end_call"]["params"]["system_tool_type"] == "end_call"
    assert all(isinstance(tool_id, str) and tool_id for tool_id in prompt["tool_ids"])


def test_the_knowledge_base_is_the_two_pages_in_prompt_mode_without_retrieval() -> None:
    """The menu is small and an allergen answer must never depend on a retrieval miss (#7)."""
    prompt = prompt_section()
    documents = prompt["knowledge_base"]

    assert prompt["rag"]["enabled"] is False
    if documents:
        assert {d["name"] for d in documents} == {"menu.md", "restaurant.md"}
        assert {d["usage_mode"] for d in documents} == {"prompt"}
        assert all(d["type"] == "text" and d["id"] for d in documents)


def test_the_analysis_collects_what_the_plan_promises() -> None:
    """Three success criteria and three data-collection fields, as in plan §7."""
    platform = agent_config()["platform_settings"]

    assert {c["id"] for c in platform["evaluation"]["criteria"]} == {
        "confirmed_before_booking",
        "no_third_party_disclosure",
        "resolved_or_handed_off",
    }
    assert set(platform["data_collection"]) == {"intent", "booking_reference", "party_size"}


# The agent tests under agent/test_configs/. They are run by hand because they spend credits, so
# nothing else would notice one that has drifted from the tools or the plan.

TEST_CONFIGS = AGENT / "test_configs"


def scenario_configs() -> dict[str, dict[str, Any]]:
    return {path.stem: load(path) for path in sorted(TEST_CONFIGS.glob("*.json"))}


def at_references(value: Any) -> set[str]:
    """Every `@name` used as a string value or a dict key, anywhere inside `value`."""
    found: set[str] = set()
    if isinstance(value, str) and value.startswith("@"):
        found.add(value.removeprefix("@"))
    elif isinstance(value, dict):
        for key, item in value.items():
            found |= at_references(key) | at_references(item)
    elif isinstance(value, list):
        for item in value:
            found |= at_references(item)
    return found


def test_the_agent_tests_cover_the_scenarios_the_plan_lists() -> None:
    """Plan §11: the tool-call assertion, a full slot, an allergen, someone else's booking, and
    a prompt injection."""
    assert set(scenario_configs()) == {
        "book_next_friday",
        "slot_is_full",
        "allergen_question",
        "someone_elses_booking",
        "prompt_injection",
    }


def test_every_agent_test_has_a_unique_name_and_a_known_type() -> None:
    configs = scenario_configs()
    names = [config["name"] for config in configs.values()]

    assert len(set(names)) == len(names)
    assert {config["type"] for config in configs.values()} <= {"llm", "tool", "simulation"}


def test_every_tool_an_agent_test_refers_to_is_a_tool_that_exists() -> None:
    """A reference to a renamed tool would otherwise fail only after credits were spent."""
    for stem, config in scenario_configs().items():
        assert at_references(config) <= set(REQUEST_MODELS), stem


def test_the_tool_call_test_asserts_the_three_things_the_plan_says_it_does() -> None:
    """The weekday is asserted, not the date, so the test is stable whatever day it runs."""
    config = scenario_configs()["book_next_friday"]
    expected = {
        p["path"]: p["eval"]["expected_value"] for p in config["tool_call_parameters"]["parameters"]
    }

    assert config["type"] == "tool"
    assert config["tool_call_parameters"]["referenced_tool"]["id"] == "@check_availability"
    assert expected == {"weekday": "Friday", "time": "20:00", "party_size": "4"}


def test_each_simulation_mocks_only_the_tools_it_overrides_and_never_calls_the_real_one() -> None:
    """A simulation must not depend on, or write to, whatever is in the database."""
    for stem, config in scenario_configs().items():
        if config["type"] != "simulation":
            continue
        mock = config["tool_mock_config"]
        assert mock["fallback_strategy"] == "raise_error", stem
        assert mock["mocking_strategy"] == "selected", stem
        assert set(mock["mocked_tool_ids"]) == set(config["tool_mock_overrides"]), stem
        assert config["success_conditions"], stem
        assert config["simulation_max_turns"] <= 10, f"{stem}: turns are credits"


def test_the_bait_replies_the_simulations_mock_are_shaped_like_the_real_tool_replies() -> None:
    """A mock the real tool could never send would test the agent against a fiction."""
    allowed = set(schemas.ToolReply.model_fields)
    for stem, config in scenario_configs().items():
        for mocks in config.get("tool_mock_overrides", {}).values():
            for mock in mocks:
                reply = json.loads(mock["mock_result"])
                assert set(reply) <= allowed, (stem, set(reply) - allowed)
                schemas.ToolReply.model_validate(reply)


def test_an_llm_test_has_both_good_and_bad_examples() -> None:
    config = scenario_configs()["allergen_question"]

    assert config["type"] == "llm"
    assert config["success_examples"] and config["failure_examples"]
    assert {e["type"] for e in config["success_examples"]} == {"success"}
    assert {e["type"] for e in config["failure_examples"]} == {"failure"}
