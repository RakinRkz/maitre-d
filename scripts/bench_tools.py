"""Measure tool latency against a running server, calling the tools the way the agent does.

    uv run python scripts/bench_tools.py --base-url http://localhost:8000 --rounds 200

Each round runs one booking's life: check availability, create, find, change, cancel. Requests
are sequential, one at a time like a single conversation, so this measures latency rather than
throughput. It prints two views of the same requests: the round trip seen by this client, and
the server time the app recorded in `tool_invocations`. The gap between them is the network.

Rows it creates are tagged with a `bench_` conversation ID and deleted at the end.
"""

import argparse
import asyncio
import random
import statistics
import time
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from sqlalchemy import text

from maitre_d import timeslots
from maitre_d.config import get_settings
from maitre_d.db import make_engine
from maitre_d.speech import WEEKDAYS

WARM_UP_ROUNDS = 10


def percentiles(samples: list[float]) -> tuple[float, float, float]:
    cuts = statistics.quantiles(samples, n=100, method="inclusive")
    return cuts[49], cuts[94], max(samples)


def bookable_slots(days: int) -> list[tuple[str, str, str]]:
    """(date, weekday, time) for every dinner start for two over the coming days."""
    today = timeslots.today_in_london(datetime.now(UTC))
    slots = []
    for offset in range(2, 2 + days):
        day = today + timedelta(days=offset)
        for start in timeslots.candidate_starts(day, 2):
            slots.append((day.isoformat(), WEEKDAYS[day.weekday()], f"{start:%H:%M}"))
    return slots


async def run(base_url: str, rounds: int) -> None:
    settings = get_settings()
    secret = settings.tool_bearer_secret
    # The secret is optional in Settings so Alembic and seed can run without it; the bench cannot,
    # because every tool call it makes needs one.
    if secret is None or not secret.get_secret_value().strip():
        raise SystemExit("TOOL_BEARER_SECRET must be set to a non-empty value to run the bench.")
    headers = {"Authorization": f"Bearer {secret.get_secret_value()}"}
    client_ms: dict[str, list[float]] = defaultdict(list)
    rng = random.Random(1)
    slots = bookable_slots(days=14)

    async with httpx.AsyncClient(base_url=base_url, headers=headers, timeout=10) as client:

        async def call(tool: str, body: dict[str, Any], keep: bool) -> dict[str, Any]:
            started = time.perf_counter()
            response = await client.post(f"/tools/{tool}", json=body)
            elapsed = (time.perf_counter() - started) * 1000
            response.raise_for_status()
            if keep:
                client_ms[tool].append(elapsed)
            reply: dict[str, Any] = response.json()
            return reply

        for i in range(WARM_UP_ROUNDS + rounds):
            keep = i >= WARM_UP_ROUNDS
            date, weekday, at = rng.choice(slots)
            guest = {
                "conversation_id": f"bench_{i}",
                "name": f"Bench Guest {i}",
                "phone": f"07700 900{i % 1000:03d}",
            }
            when = {"date": date, "weekday": weekday, "time": at, "party_size": 2}
            await call(
                "check_availability", {"conversation_id": guest["conversation_id"], **when}, keep
            )
            created = await call("create_booking", guest | when, keep)
            if not created["ok"]:
                continue
            reference = created["booking"]["reference"]
            await call("find_booking", guest, keep)
            new_time = (datetime.strptime(at, "%H:%M") - timedelta(minutes=15)).strftime("%H:%M")
            await call("change_booking", guest | {"reference": reference, "time": new_time}, keep)
            await call("cancel_booking", guest | {"reference": reference}, keep)

    engine = make_engine(settings.database_url)
    async with engine.begin() as connection:
        rows = await connection.execute(
            text(
                "SELECT tool, server_ms FROM tool_invocations "
                "WHERE conversation_id LIKE 'bench\\_%' AND conversation_id NOT IN "
                "(SELECT 'bench_' || g FROM generate_series(0, :warm - 1) g)"
            ),
            {"warm": WARM_UP_ROUNDS},
        )
        server_ms: dict[str, list[float]] = defaultdict(list)
        for row in rows:
            server_ms[row.tool].append(row.server_ms)
        await connection.execute(
            text("DELETE FROM bookings WHERE conversation_id LIKE 'bench\\_%'")
        )
        await connection.execute(
            text("DELETE FROM tool_invocations WHERE conversation_id LIKE 'bench\\_%'")
        )
    await engine.dispose()

    print(f"{rounds} rounds after {WARM_UP_ROUNDS} warm-up rounds against {base_url}")
    print(f"{'tool':<20}{'n':>5}  {'client p50/p95/max ms':>24}  {'server p50/p95/max ms':>24}")
    for tool in (
        "check_availability",
        "create_booking",
        "find_booking",
        "change_booking",
        "cancel_booking",
    ):
        c50, c95, cmax = percentiles(client_ms[tool])
        s50, s95, smax = percentiles(server_ms[tool])
        print(
            f"{tool:<20}{len(client_ms[tool]):>5}  "
            f"{c50:>8.1f}{c95:>8.1f}{cmax:>8.1f}  {s50:>8.1f}{s95:>8.1f}{smax:>8.1f}"
        )


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument("--base-url", default="http://localhost:8000")
    parser.add_argument("--rounds", type=int, default=200)
    args = parser.parse_args()
    asyncio.run(run(args.base_url, args.rounds))


if __name__ == "__main__":
    main()
