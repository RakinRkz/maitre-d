"""Saffron Table's facts: the only definition of its timezone, hours and tables.

Everything that decides whether a booking is possible reads from here, and the knowledge-base
page the agent quotes hours from is generated from here, so the agent cannot promise a time the
backend would refuse.
"""

from dataclasses import dataclass
from datetime import date, time, timedelta
from zoneinfo import ZoneInfo

NAME = "Saffron Table"
TIMEZONE = ZoneInfo("Europe/London")


@dataclass(frozen=True)
class Service:
    name: str
    opens: time
    closes: time


LUNCH = Service("lunch", time(12, 0), time(15, 0))
DINNER = Service("dinner", time(17, 30), time(22, 30))

# Indexed by date.weekday(): Monday is 0. Closed on Mondays.
SERVICES_BY_WEEKDAY: dict[int, tuple[Service, ...]] = {
    0: (),
    1: (LUNCH, DINNER),
    2: (LUNCH, DINNER),
    3: (LUNCH, DINNER),
    4: (LUNCH, DINNER),
    5: (LUNCH, DINNER),
    6: (LUNCH, DINNER),
}

# (month, day): closed every year on these dates whatever the weekday.
CLOSED_DAYS_OF_YEAR: frozenset[tuple[int, int]] = frozenset({(12, 25), (12, 26), (1, 1)})


@dataclass(frozen=True)
class DiningTable:
    id: int
    label: str
    seats: int


TABLES: tuple[DiningTable, ...] = (
    DiningTable(1, "T1", 2),
    DiningTable(2, "T2", 2),
    DiningTable(3, "T3", 2),
    DiningTable(4, "T4", 2),
    DiningTable(5, "T5", 4),
    DiningTable(6, "T6", 4),
    DiningTable(7, "T7", 4),
    DiningTable(8, "T8", 4),
    DiningTable(9, "T9", 6),
    DiningTable(10, "T10", 8),
)

MAX_PARTY_SIZE = max(table.seats for table in TABLES)
SLOT_MINUTES = 15
BOOKING_HORIZON = timedelta(days=60)
MIN_NOTICE = timedelta(minutes=15)

# How far either side of a full slot to look for alternatives, how many to offer, and how many
# later days to try when the requested day has nothing near the requested time.
ALTERNATIVE_WINDOW = timedelta(minutes=90)
MAX_ALTERNATIVES = 3
LATER_DAYS_TO_SEARCH = 7


def sitting_length(party_size: int) -> timedelta:
    """How long a table is held. Larger parties stay longer."""
    return timedelta(minutes=90 if party_size <= 4 else 120)


def services_on(day: date) -> tuple[Service, ...]:
    if (day.month, day.day) in CLOSED_DAYS_OF_YEAR:
        return ()
    return SERVICES_BY_WEEKDAY[day.weekday()]


def tables_for(party_size: int) -> list[DiningTable]:
    """Tables that seat the party, smallest first, so a couple doesn't take the eight-top."""
    return sorted((t for t in TABLES if t.seats >= party_size), key=lambda t: (t.seats, t.id))
