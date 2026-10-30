"""Load Saffron Table's dining tables and, for a demo, a week of fictional bookings.

    uv run python scripts/seed.py                  # tables only; safe to run any number of times
    uv run python scripts/seed.py --demo           # plus a week of fictional bookings from today
    uv run python scripts/seed.py --demo --reset   # delete all bookings first

Every guest is invented and every phone number is in the 07700 900000-900999 range that Ofcom
reserves for drama, so nothing here can reach a real person.

--demo also adds two fictional calls and a caller message, with IDs starting `demo_`, so the
dashboard has something to show before a real call. They carry no timings: the project reports
only measured numbers, and a made-up latency in a screenshot would be one.
"""

import argparse
import asyncio
import json
import random
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert

from maitre_d import bookings, restaurant, timeslots
from maitre_d.config import get_settings
from maitre_d.db import make_engine, make_sessionmaker, sync_dining_tables
from maitre_d.models import Booking, Call, CallerMessage

GUESTS = (
    "Nadia Ahmed",
    "Tom Gallagher",
    "Priya Raman",
    "Kwame Mensah",
    "Sofia Marchetti",
    "Ewan Fraser",
    "Mei Lin",
    "Olu Adeyemi",
    "Hannah Becker",
    "Rafael Ortiz",
    "Aisling Byrne",
    "Yusuf Kaya",
)
NOTES = (None, None, None, "Birthday", "Window seat if possible", "One vegan guest", "Nut allergy")
BOOKINGS_PER_DAY = 14
DEMO_PREFIX = "demo_"
DEMO_AGENT_ID = "demo_agent"


def _turn(
    role: str,
    message: str | None,
    at: int,
    *,
    calls: list[dict[str, Any]] | None = None,
    results: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "role": role,
        "message": message,
        "time_in_call_secs": at,
        "tool_calls": calls,
        "tool_results": results,
    }


def _call(request_id: str, tool: str, params: dict[str, Any]) -> dict[str, Any]:
    return {
        "request_id": request_id,
        "tool_name": tool,
        "params_as_json": json.dumps(params),
        "tool_has_been_called": True,
    }


def _result(request_id: str, tool: str, reply: dict[str, Any]) -> dict[str, Any]:
    # No `tool_latency_secs`: a demo has no measurement to report.
    return {
        "request_id": request_id,
        "tool_name": tool,
        "result_value": json.dumps(reply),
        "is_error": False,
    }


def demo_calls(now: datetime) -> tuple[list[Call], list[CallerMessage]]:
    """Two fictional finished calls, and the message one of them left for the staff."""
    slot = {"date": "2026-11-06", "weekday": "Friday", "time": "20:00", "party_size": 4}
    guest = {"name": "Priya Raman", "phone": "07700 900102"}
    available = {"ok": True, "available": True, "say": "Yes, we have a table for 4."}
    booked = {"ok": True, "say": "Booked: a table for 4, Friday at 8 pm."}
    booking_call = Call(
        conversation_id=f"{DEMO_PREFIX}booking",
        agent_id=DEMO_AGENT_ID,
        started_at=now - timedelta(hours=3),
        duration_secs=74,
        call_successful="success",
        summary="Demo call: the caller booked a table for four on Friday at 8 pm.",
        transcript=[
            _turn("agent", "Thank you for calling Saffron Table. How can I help?", 0),
            _turn("user", "Hi, a table for four on Friday around eight, please.", 4),
            _turn(
                "agent", "Let me check that.", 9, calls=[_call("req_1", "check_availability", slot)]
            ),
            _turn("agent", None, 10, results=[_result("req_1", "check_availability", available)]),
            _turn("agent", "Yes, we do. Could I take a name and number?", 14),
            _turn("user", "Priya Raman, 07700 900102.", 19),
            _turn("agent", "A table for four, Friday at eight, for Priya. Shall I book that?", 27),
            _turn("user", "Yes please.", 33),
            _turn(
                "agent",
                "One moment.",
                35,
                calls=[_call("req_2", "create_booking", {**slot, **guest})],
            ),
            _turn("agent", None, 36, results=[_result("req_2", "create_booking", booked)]),
            _turn("agent", "That is booked. Your reference is K7M2QX.", 61),
            _turn("user", "Thanks, bye.", 68),
            _turn("agent", "Goodbye!", 70, calls=[_call("req_3", "end_call", {})]),
        ],
        analysis={"call_successful": "success"},
        call_metadata={"call_duration_secs": 74},
    )
    message_call = Call(
        conversation_id=f"{DEMO_PREFIX}message",
        agent_id=DEMO_AGENT_ID,
        started_at=now - timedelta(hours=1),
        duration_secs=52,
        call_successful="success",
        summary="Demo call: a party too large for any table, so the agent took a message.",
        transcript=[
            _turn("agent", "Thank you for calling Saffron Table. How can I help?", 0),
            _turn("user", "I would like to book for twelve people next month.", 5),
            _turn(
                "agent",
                "That is larger than our biggest table. I can take a message for the events team.",
                11,
            ),
            _turn("user", "Please do. Kwame Mensah, 07700 900103.", 20),
            _turn("agent", "Thank you, the team will be in touch.", 41),
        ],
        analysis={"call_successful": "success"},
        call_metadata={"call_duration_secs": 52},
    )
    message = CallerMessage(
        conversation_id=message_call.conversation_id,
        caller_name="Kwame Mensah",
        caller_phone="+447700900103",
        message="Would like to book for twelve people next month; please call back.",
        created_at=now - timedelta(minutes=58),
    )
    return [booking_call, message_call], [message]


async def _add_demo_calls(session: Any, now: datetime) -> int:
    """Insert the demo calls and message, leaving any that are already there untouched."""
    calls, messages = demo_calls(now)
    for call in calls:
        await session.execute(
            insert(Call)
            .values(
                conversation_id=call.conversation_id,
                agent_id=call.agent_id,
                started_at=call.started_at,
                duration_secs=call.duration_secs,
                call_successful=call.call_successful,
                summary=call.summary,
                transcript=call.transcript,
                analysis=call.analysis,
                call_metadata=call.call_metadata,
            )
            .on_conflict_do_nothing()
        )
    already = await session.scalar(
        select(func.count())
        .select_from(CallerMessage)
        .where(CallerMessage.conversation_id.like(f"{DEMO_PREFIX}%"))
    )
    if not already:
        session.add_all(messages)
    return len(calls)


async def seed(demo: bool, reset: bool, seed_value: int) -> None:
    engine = make_engine(get_settings().database_url)
    async with engine.begin() as connection:
        await sync_dining_tables(connection)
    print(f"dining tables: {len(restaurant.TABLES)}")

    if demo:
        rng = random.Random(seed_value)
        now = datetime.now(UTC)
        today = timeslots.today_in_london(now)
        earliest = timeslots.earliest_bookable(now)
        async with make_sessionmaker(engine)() as session:
            if reset:
                await session.execute(delete(Booking))
                await session.execute(
                    delete(Call).where(Call.conversation_id.like(f"{DEMO_PREFIX}%"))
                )
                await session.execute(
                    delete(CallerMessage).where(
                        CallerMessage.conversation_id.like(f"{DEMO_PREFIX}%")
                    )
                )
            created = 0
            for offset in range(7):
                day = today + timedelta(days=offset)
                occupied = await bookings.load_occupied(session, day, day)
                for _ in range(BOOKINGS_PER_DAY):
                    party_size = rng.choice((2, 2, 2, 3, 4, 4, 5, 6))
                    starts = [
                        s for s in timeslots.candidate_starts(day, party_size) if s >= earliest
                    ]
                    if not starts:
                        break
                    start = rng.choice(starts)
                    tables = bookings.free_tables(occupied, start, party_size)
                    if not tables:
                        continue
                    end = start + restaurant.sitting_length(party_size)
                    guest = rng.randrange(len(GUESTS))
                    session.add(
                        Booking(
                            reference=bookings.new_reference(),
                            table_id=tables[0].id,
                            party_size=party_size,
                            starts_at=start,
                            ends_at=end,
                            guest_name=GUESTS[guest],
                            guest_phone=f"+447700900{100 + guest:03d}",
                            notes=rng.choice(NOTES),
                            status="confirmed",
                            conversation_id="seed",
                        )
                    )
                    occupied.append(bookings.Occupied(tables[0].id, start, end))
                    created += 1
            demo_call_count = await _add_demo_calls(session, now)
            await session.commit()
        print(f"demo bookings: {created} over the 7 days from {today}")
        print(f"demo calls: {demo_call_count}, with one caller message")

    await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument("--demo", action="store_true", help="add a week of fictional bookings")
    parser.add_argument("--reset", action="store_true", help="delete all bookings first")
    parser.add_argument("--seed", type=int, default=7, help="random seed for --demo")
    args = parser.parse_args()
    asyncio.run(seed(args.demo, args.reset, args.seed))


if __name__ == "__main__":
    main()
