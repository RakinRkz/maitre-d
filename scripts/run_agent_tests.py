"""Run the agent tests and report what they cost.

    uv run python scripts/run_agent_tests.py                              # list them; run nothing
    uv run python scripts/run_agent_tests.py book_next_friday --confirm-spend
    uv run python scripts/run_agent_tests.py --all --confirm-spend

Running a test spends the workspace's credits, and the platform does not document how tests are
billed: each finished run reports `credits_used`, so run one first, read the figure, and only then
decide how many of the rest to run. That is why nothing runs without `--confirm-spend`, and why
the tests are never on a schedule or in CI.

The tests must already be on the platform (`scripts/setup_elevenlabs.py` puts them there) and the
agent's ID must be in `.env`. Results are printed, not stored: record a measured cost in the
README by hand, with the date and what was run.
"""

import argparse
import asyncio
import json
import time
from pathlib import Path
from typing import Any

import httpx

from maitre_d.config import Settings
from maitre_d.elevenlabs import (
    ElevenLabsError,
    find_test_id,
    get_test_invocation,
    make_client,
    start_test_run,
)

TEST_DIR = Path(__file__).resolve().parents[1] / "agent" / "test_configs"
POLL_SECONDS = 3.0
GIVE_UP_AFTER_SECONDS = 240.0


def available_tests() -> dict[str, str]:
    """File name without `.json` -> the test's name on the platform."""
    return {
        path.stem: json.loads(path.read_text(encoding="utf-8"))["name"]
        for path in sorted(TEST_DIR.glob("*.json"))
    }


def summarise(invocation: dict[str, Any]) -> tuple[list[tuple[str, str, int | None]], bool]:
    """`(rows, finished)` from an invocation: each run's name, status and credits used."""
    runs = invocation.get("test_runs")
    rows: list[tuple[str, str, int | None]] = []
    for run in runs if isinstance(runs, list) else []:
        if not isinstance(run, dict):
            continue
        credits = run.get("credits_used")
        rows.append(
            (
                str(run.get("test_name", "?")),
                str(run.get("status", "?")),
                credits if isinstance(credits, int) else None,
            )
        )
    finished = bool(rows) and all(status != "pending" for _, status, _ in rows)
    return rows, finished


async def run(
    client: httpx.AsyncClient, agent_id: str, names: list[str]
) -> list[tuple[str, str, int | None]]:
    """Start the named tests and wait for them. Takes the client so a test can pass a fake."""
    test_ids: list[str] = []
    for name in names:
        test_id = await find_test_id(client, name)
        if test_id is None:
            raise SystemExit(f"Test {name!r} is not on the platform; run setup_elevenlabs.py.")
        test_ids.append(test_id)
    invocation_id = await start_test_run(
        client, agent_id=agent_id, test_ids=test_ids, name="run_agent_tests.py"
    )
    print(f"started {len(test_ids)} test(s); waiting for them to finish")
    deadline = time.monotonic() + GIVE_UP_AFTER_SECONDS
    while True:
        rows, finished = summarise(await get_test_invocation(client, invocation_id))
        if finished:
            return rows
        if time.monotonic() > deadline:
            raise SystemExit("Gave up waiting; check the invocation in the dashboard.")
        await asyncio.sleep(POLL_SECONDS)


async def run_with_key(
    api_key: str, agent_id: str, names: list[str]
) -> list[tuple[str, str, int | None]]:
    async with make_client(api_key) as client:
        return await run(client, agent_id, names)


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("tests", nargs="*", help="test file names to run, without .json")
    parser.add_argument("--all", action="store_true", help="run every test")
    parser.add_argument(
        "--confirm-spend", action="store_true", help="acknowledge that this spends credits"
    )
    args = parser.parse_args()

    tests = available_tests()
    unknown = [name for name in args.tests if name not in tests]
    if unknown:
        raise SystemExit(f"Unknown test(s): {', '.join(unknown)}. Available: {', '.join(tests)}")
    chosen = list(tests) if args.all else list(args.tests)

    if not chosen:
        print("Available tests (nothing was run):")
        for stem, name in tests.items():
            print(f"  {stem}: {name}")
        return
    if not args.confirm_spend:
        raise SystemExit(
            f"This would run {len(chosen)} test(s) and spend credits. Add --confirm-spend."
        )

    settings = Settings()
    key, agent_id = settings.elevenlabs_api_key, settings.elevenlabs_agent_id
    if key is None or not key.get_secret_value().strip() or not agent_id:
        raise SystemExit("ELEVENLABS_API_KEY and ELEVENLABS_AGENT_ID must be set in .env.")
    try:
        rows = asyncio.run(
            run_with_key(key.get_secret_value(), agent_id, [tests[n] for n in chosen])
        )
    except ElevenLabsError as exc:
        raise SystemExit(str(exc)) from None
    except httpx.HTTPError as exc:
        raise SystemExit(f"Could not reach ElevenLabs ({type(exc).__name__}).") from None

    for name, status, credits in rows:
        print(
            f"{status:8} {name}  credits_used={credits if credits is not None else 'not reported'}"
        )
    reported = [c for _, _, c in rows if c is not None]
    print(f"total credits reported: {sum(reported)} across {len(reported)} of {len(rows)} test(s)")
    if any(status != "passed" for _, status, _ in rows):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
