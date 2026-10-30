"""Load Saffron Table's dining tables and, for a demo, a week of fictional bookings.

    uv run python scripts/seed.py                  # tables only; safe to run any number of times
    uv run python scripts/seed.py --demo           # plus a week of fictional bookings from today
    uv run python scripts/seed.py --demo --reset   # delete all bookings first

Every guest is invented and every phone number is in the 07700 900000-900999 range that Ofcom
reserves for drama, so nothing here can reach a real person.
"""

import argparse
import asyncio
import random
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete

from maitre_d import bookings, restaurant, timeslots
from maitre_d.config import get_settings
from maitre_d.db import make_engine, make_sessionmaker, sync_dining_tables
from maitre_d.models import Booking

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
            await session.commit()
        print(f"demo bookings: {created} over the 7 days from {today}")

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
