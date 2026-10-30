"""Create or update everything the agent needs in your ElevenLabs workspace.

    uv run python scripts/setup_elevenlabs.py --dry-run   # the plan; no network, nothing written
    uv run python scripts/setup_elevenlabs.py             # do it
    uv run python scripts/setup_elevenlabs.py --skip-secret   # everything but the secret

From the files in this repo it creates, or updates by name:

  - the workspace secret `tool_bearer`, holding "Bearer <TOOL_BEARER_SECRET>", and a secret-type
    environment variable of the same label that the tool configs' Authorization header points at;
  - the `backend_host` environment variable, with a placeholder if it does not exist yet (the
    tool URLs are `https://{{system__env_backend_host}}/tools/...`; `set_backend_host.py` sets it
    once there is a tunnel or a deployment);
  - the two knowledge-base documents in knowledge/;
  - the six server tools in agent/tool_configs/;
  - the agent tests in agent/test_configs/, with each `@tool_name` replaced by that tool's ID;
  - the agent in agent/agent_configs/, with those tools, documents and tests attached.

`--skip-secret` leaves the first item to you, for anyone who would rather create the secret and
its variable in the ElevenLabs dashboard than have a script write the bearer there: the secret
must hold "Bearer <TOOL_BEARER_SECRET>", and a secret-type variable `tool_bearer` must point at it.

It is safe to run again: every object is found by name and updated, so after editing the prompt
(then `scripts/sync_agent_prompt.py`), a tool config or the menu, running it again is how the change
reaches the platform. The IDs the platform assigns to tools and documents differ per account, so
they are put into the request here and never written to the repo. The agent's own ID is written to
`.env`, which is git-ignored.
"""

import argparse
import asyncio
import copy
import json
from pathlib import Path
from typing import Any

import httpx

from maitre_d.config import Settings
from maitre_d.elevenlabs import (
    ElevenLabsError,
    ensure_secret,
    ensure_secret_environment_variable,
    ensure_text_document_outcome,
    environment_variable_exists,
    make_client,
    set_environment_variable,
    upsert_agent,
    upsert_test,
    upsert_tool,
)
from maitre_d.envfile import set_env_value

REPO_ROOT = Path(__file__).resolve().parents[1]
AGENT_DIR = REPO_ROOT / "agent"
KNOWLEDGE_DIR = REPO_ROOT / "knowledge"
ENV_FILE = REPO_ROOT / ".env"

DOCUMENT_NAMES = ("menu.md", "restaurant.md")
# The secret and its environment variable share a name; the tool configs refer to it by label.
BEARER_NAME = "tool_bearer"
BACKEND_HOST_LABEL = "backend_host"
# `.invalid` is reserved and never resolves, so a tool call made before a real host is set fails
# at once instead of reaching someone else's server.
PLACEHOLDER_HOST = "tunnel-not-started.invalid"


def load_tool_configs(directory: Path = AGENT_DIR / "tool_configs") -> dict[str, dict[str, Any]]:
    """The tool configs by tool name, in name order."""
    configs: dict[str, dict[str, Any]] = {}
    for path in sorted(directory.glob("*.json")):
        config: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
        configs[str(config["name"])] = config
    return configs


def load_test_configs(directory: Path = AGENT_DIR / "test_configs") -> dict[str, dict[str, Any]]:
    """The agent test configs by their file name without `.json`, in name order."""
    return {
        path.stem: json.loads(path.read_text(encoding="utf-8"))
        for path in sorted(directory.glob("*.json"))
    }


def resolve_tool_refs(value: Any, tool_ids: dict[str, str]) -> Any:
    """`value` with every `@tool_name` (as a value or a dict key) replaced by that tool's ID.

    A test refers to a tool by ID, which differs per workspace, so the committed files say
    `@check_availability` and the ID is filled in here. A name that is not a tool is an error
    rather than a string sent to the platform.
    """
    if isinstance(value, str) and value.startswith("@"):
        return _tool_id_for(value, tool_ids)
    if isinstance(value, dict):
        return {
            (_tool_id_for(k, tool_ids) if k.startswith("@") else k): resolve_tool_refs(v, tool_ids)
            for k, v in value.items()
        }
    if isinstance(value, list):
        return [resolve_tool_refs(item, tool_ids) for item in value]
    return value


def _tool_id_for(reference: str, tool_ids: dict[str, str]) -> str:
    name = reference.removeprefix("@")
    if name not in tool_ids:
        raise ValueError(f"{reference!r} is not one of the tools: {', '.join(tool_ids)}")
    return tool_ids[name]


def build_agent_payload(
    config: dict[str, Any],
    *,
    tool_ids: list[str],
    document_ids: dict[str, str],
    test_ids: list[str] | None = None,
) -> dict[str, Any]:
    """The agent config with this account's tool and document IDs attached.

    The committed config keeps both lists empty, because the IDs belong to one workspace. The
    input is not changed, so the same loaded config can be shown in a dry run and then sent.
    """
    payload = copy.deepcopy(config)
    prompt = payload["conversation_config"]["agent"]["prompt"]
    prompt["tool_ids"] = list(tool_ids)
    # Always in the prompt, never retrieved: an allergen answer must not depend on a retrieval miss.
    prompt["knowledge_base"] = [
        {"type": "text", "name": name, "id": document_ids[name], "usage_mode": "prompt"}
        for name in DOCUMENT_NAMES
    ]
    if test_ids:
        testing = payload.setdefault("platform_settings", {}).setdefault("testing", {})
        testing["attached_tests"] = [{"test_id": test_id} for test_id in test_ids]
    return payload


async def provision(
    client: httpx.AsyncClient,
    *,
    bearer: str,
    agent_id: str | None,
    agent_config: dict[str, Any],
    tool_configs: dict[str, dict[str, Any]],
    documents: dict[str, str],
    manage_secret: bool = True,
    test_configs: dict[str, dict[str, Any]] | None = None,
) -> str:
    """Do the work and return the agent's ID. Prints each step; never prints a secret."""
    if manage_secret:
        secret_id = await ensure_secret(client, name=BEARER_NAME, value=f"Bearer {bearer}")
        await ensure_secret_environment_variable(client, label=BEARER_NAME, secret_id=secret_id)
        print(f"secret and variable {BEARER_NAME!r}: set")
    else:
        print(f"secret and variable {BEARER_NAME!r}: skipped; they must already exist")

    if not await environment_variable_exists(client, BACKEND_HOST_LABEL):
        await set_environment_variable(
            client, label=BACKEND_HOST_LABEL, environment="production", value=PLACEHOLDER_HOST
        )
        print(f"{BACKEND_HOST_LABEL}: created with a placeholder; run set_backend_host.py")
    else:
        print(f"{BACKEND_HOST_LABEL}: already set, left alone")

    document_ids: dict[str, str] = {}
    for name in DOCUMENT_NAMES:
        document_ids[name], created = await ensure_text_document_outcome(
            client, name=name, text=documents[name]
        )
        print(f"{name}: {'created' if created else 'reused'}")

    tool_ids_by_name: dict[str, str] = {}
    for name, config in tool_configs.items():
        tool_ids_by_name[name] = await upsert_tool(client, config=config)
        print(f"tool {name}: set")

    test_ids: list[str] = []
    for name, test in (test_configs or {}).items():
        test_ids.append(await upsert_test(client, config=resolve_tool_refs(test, tool_ids_by_name)))
        print(f"test {name}: set")

    payload = build_agent_payload(
        agent_config,
        tool_ids=list(tool_ids_by_name.values()),
        document_ids=document_ids,
        test_ids=test_ids,
    )
    new_id = await upsert_agent(client, agent_id=agent_id, config=payload)
    print(f"agent: {'updated' if new_id == agent_id else 'created'}")
    return new_id


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="print the plan; make no network call, write nothing"
    )
    parser.add_argument(
        "--skip-secret",
        action="store_true",
        help="don't write the bearer to the workspace; create the secret and variable yourself",
    )
    args = parser.parse_args()

    agent_config: dict[str, Any] = json.loads(
        (AGENT_DIR / "agent_configs" / "saffron_table.json").read_text(encoding="utf-8")
    )
    tool_configs = load_tool_configs()
    test_configs = load_test_configs()
    documents = {
        name: (KNOWLEDGE_DIR / name).read_text(encoding="utf-8") for name in DOCUMENT_NAMES
    }

    if args.dry_run:
        if not args.skip_secret:
            print(f"would set the secret and variable {BEARER_NAME!r} from TOOL_BEARER_SECRET")
        print(f"would create {BACKEND_HOST_LABEL!r} if missing")
        print(f"would upload or reuse: {', '.join(DOCUMENT_NAMES)}")
        print(f"would create or update tools: {', '.join(tool_configs)}")
        print(f"would create or update tests: {', '.join(test_configs)}")
        print(f"would create or update the agent {agent_config['name']!r}")
        return

    settings = Settings()
    api_key, bearer = settings.elevenlabs_api_key, settings.tool_bearer_secret
    if api_key is None or not api_key.get_secret_value().strip():
        raise SystemExit("ELEVENLABS_API_KEY must be set in .env or the environment.")
    if bearer is None or not bearer.get_secret_value().strip():
        raise SystemExit("TOOL_BEARER_SECRET must be set in .env or the environment.")

    async def run() -> str:
        async with make_client(api_key.get_secret_value()) as client:
            return await provision(
                client,
                bearer=bearer.get_secret_value(),
                agent_id=settings.elevenlabs_agent_id,
                agent_config=agent_config,
                tool_configs=tool_configs,
                documents=documents,
                manage_secret=not args.skip_secret,
                test_configs=test_configs,
            )

    try:
        agent_id = asyncio.run(run())
    except ElevenLabsError as exc:
        raise SystemExit(str(exc)) from None
    except httpx.HTTPError as exc:
        # Only the class is printed: the exception text can include the URL.
        raise SystemExit(f"Could not reach ElevenLabs ({type(exc).__name__}).") from None

    if agent_id != settings.elevenlabs_agent_id:
        set_env_value(ENV_FILE, "ELEVENLABS_AGENT_ID", agent_id)
        print("ELEVENLABS_AGENT_ID written to .env")
    print("done. Next: start the backend and a tunnel, then run scripts/set_backend_host.py")


if __name__ == "__main__":
    main()
