"""The demo data: fictional by construction, and readable by the dashboard.

The project reports only measured numbers, so the rule worth a test is that the seeded calls
carry none: a made-up latency in a screenshot would be an invented metric.
"""

import importlib.util
import json
import re
from datetime import UTC, datetime
from pathlib import Path
from types import ModuleType

from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from maitre_d.models import Call, CallerMessage

NOW = datetime(2026, 10, 30, 12, 0, tzinfo=UTC)
# Ofcom reserves 07700 900000-900999 for drama; in E.164 that is +44 7700 900xxx.
FICTIONAL_PHONE = re.compile(r"(?:07700 ?900\d{3}|\+447700900\d{3})")


def load_seed() -> ModuleType:
    path = Path(__file__).resolve().parents[1] / "scripts" / "seed.py"
    spec = importlib.util.spec_from_file_location("seed", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


seed = load_seed()


def test_the_demo_calls_carry_no_timings_because_nothing_was_measured() -> None:
    calls, _ = seed.demo_calls(NOW)

    text = json.dumps([c.transcript for c in calls])

    assert "tool_latency_secs" not in text
    assert "conversation_turn_metrics" not in text


def test_every_demo_id_is_marked_and_every_phone_number_is_in_the_drama_range() -> None:
    calls, messages = seed.demo_calls(NOW)

    assert all(c.conversation_id.startswith("demo_") for c in calls)
    assert all(m.conversation_id.startswith("demo_") for m in messages)
    everything = json.dumps([c.transcript for c in calls]) + " ".join(
        m.caller_phone for m in messages
    )
    numbers = re.findall(r"\+?\d[\d ]{8,}\d", everything)
    assert numbers, "the demo should contain a phone number to check"
    assert all(FICTIONAL_PHONE.fullmatch(n.strip()) for n in numbers), numbers


def test_each_demo_tool_result_answers_a_tool_call_made_in_the_same_call() -> None:
    """The dashboard pairs results to calls by request_id, so the demo must be well formed."""
    calls, _ = seed.demo_calls(NOW)

    for call in calls:
        made = {tc["request_id"] for turn in call.transcript for tc in (turn["tool_calls"] or [])}
        answered = {
            tr["request_id"] for turn in call.transcript for tr in (turn["tool_results"] or [])
        }
        assert answered <= made, call.conversation_id


async def test_seeding_twice_leaves_one_copy_and_the_dashboard_can_read_it(
    client: AsyncClient, session: AsyncSession
) -> None:
    await seed._add_demo_calls(session, NOW)
    await session.commit()
    await seed._add_demo_calls(session, NOW)
    await session.commit()

    calls = (await session.scalar(select(func.count()).select_from(Call))) or 0
    messages = (await session.scalar(select(func.count()).select_from(CallerMessage))) or 0
    detail = await client.get("/api/calls/demo_booking")

    assert (calls, messages) == (2, 1)
    assert detail.status_code == 200
    assert len(detail.json()["transcript"]) == 13
