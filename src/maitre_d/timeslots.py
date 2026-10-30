"""London wall-clock time in, UTC instants out, with every check a booking needs on the way.

The agent supplies a local date, the weekday the caller said, and a local time. This module turns
that into an aware datetime in Europe/London or a `Problem` with a sentence the agent can say.

Two rules keep DST from biting:

- Local times are localised with zoneinfo, never by adding an offset. 19:00 on 29 March 2026 is
  18:00 UTC; 19:00 on 25 October 2026 is 19:00 UTC.
- Slot grids are stepped in wall-clock time and then localised, so a grid is right on every
  day, including the 23- and 25-hour days when the clocks change. Day boundaries are local
  midnights converted to UTC, never "midnight UTC plus 24 hours".
"""

import contextlib
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta

from maitre_d import restaurant, speech
from maitre_d.restaurant import TIMEZONE
from maitre_d.schemas import ErrorCode


class InvalidLocalTime(ValueError):
    """A wall-clock time the clocks skip or repeat, so it names no single instant."""


@dataclass(frozen=True)
class Problem:
    code: ErrorCode
    say: str


def today_in_london(now: datetime) -> date:
    """Today's date where the restaurant is. At 00:30 BST it is still yesterday in UTC."""
    return now.astimezone(TIMEZONE).date()


def local_datetime(day: date, at: time) -> datetime:
    candidate = datetime.combine(day, at, tzinfo=TIMEZONE)
    # `fold` only changes the offset of a wall-clock time the clocks skip (spring) or repeat
    # (autumn), so the two folds disagreeing is exactly the test for "no single instant".
    if candidate.utcoffset() != candidate.replace(fold=1).utcoffset():
        raise InvalidLocalTime(f"{candidate:%Y-%m-%d %H:%M} is skipped or repeated in London")
    return candidate


def day_bounds(day: date) -> tuple[datetime, datetime]:
    """The UTC instants of local midnight and the next local midnight: 23, 24 or 25 hours apart."""
    start = datetime.combine(day, time(0), tzinfo=TIMEZONE)
    end = datetime.combine(day + timedelta(days=1), time(0), tzinfo=TIMEZONE)
    return start.astimezone(UTC), end.astimezone(UTC)


def _fits_service(day: date, at: time, party_size: int) -> bool:
    sitting = restaurant.sitting_length(party_size)
    start = datetime.combine(day, at)
    return any(
        datetime.combine(day, service.opens) <= start
        and start + sitting <= datetime.combine(day, service.closes)
        for service in restaurant.services_on(day)
    )


def candidate_starts(day: date, party_size: int) -> list[datetime]:
    """Every bookable start on `day` for this party, on the quarter hour, in local time."""
    sitting = restaurant.sitting_length(party_size)
    step = timedelta(minutes=restaurant.SLOT_MINUTES)
    starts: list[datetime] = []
    for service in restaurant.services_on(day):
        wall = datetime.combine(day, service.opens)
        last = datetime.combine(day, service.closes) - sitting
        while wall <= last:
            with contextlib.suppress(InvalidLocalTime):
                starts.append(local_datetime(day, wall.time()))
            wall += step
    return starts


def earliest_bookable(now: datetime) -> datetime:
    return now.astimezone(TIMEZONE) + restaurant.MIN_NOTICE


def nearest_date_on(day: date, weekday: int, today: date) -> date:
    """The date closest to `day` that falls on `weekday`, not before today."""
    forward = (weekday - day.weekday()) % 7
    backward = forward - 7
    best = forward if abs(forward) <= abs(backward) else backward
    suggestion = day + timedelta(days=best)
    if suggestion < today:
        suggestion = day + timedelta(days=forward)
    return suggestion


def _quarter_hours_around(at: time) -> tuple[time, time]:
    minutes = at.hour * 60 + at.minute
    below = minutes - minutes % restaurant.SLOT_MINUTES
    above = below + restaurant.SLOT_MINUTES
    return time(below // 60, below % 60), time((above // 60) % 24, above % 60)


def validate_start(
    day: date, weekday: str | None, at: time, party_size: int, now: datetime
) -> datetime | Problem:
    """Turn what the agent heard into a bookable local start time, or say why it isn't one.

    The order matters: each check assumes the earlier ones passed, and the first failure is the
    one the caller hears.
    """
    today = today_in_london(now)

    if party_size > restaurant.MAX_PARTY_SIZE:
        return Problem("party_too_large", speech.party_too_large())

    # The caller says "Friday"; the model works out the date. If they disagree, the model's
    # calendar arithmetic is the likelier mistake, so ask rather than book the wrong day.
    if weekday is not None and weekday != speech.WEEKDAYS[day.weekday()]:
        suggestion = nearest_date_on(day, speech.WEEKDAYS.index(weekday), today)
        return Problem("weekday_mismatch", speech.weekday_mismatch(day, weekday, suggestion, today))

    if day < today:
        return Problem("in_past", speech.date_passed())
    last_day = today + restaurant.BOOKING_HORIZON
    if day > last_day:
        return Problem("too_far_ahead", speech.too_far_ahead(last_day, today))
    if not restaurant.services_on(day):
        return Problem("closed", speech.closed(day, today))

    try:
        start = local_datetime(day, at)
    except InvalidLocalTime:
        return Problem("invalid_time", speech.clocks_change())

    if not _fits_service(day, at, party_size):
        # "Eight" heard as 08:00 at a restaurant that opens at midday almost always meant 20:00.
        pm_guess = None
        if at.hour < 12 and _fits_service(day, time(at.hour + 12, at.minute), party_size):
            pm_guess = local_datetime(day, time(at.hour + 12, at.minute))
        return Problem("outside_hours", speech.outside_hours(day, party_size, pm_guess))

    if at.minute % restaurant.SLOT_MINUTES or at.second or at.microsecond:
        options = [t for t in _quarter_hours_around(at) if _fits_service(day, t, party_size)]
        return Problem("invalid_time", speech.not_on_quarter_hour(options))

    earliest = earliest_bookable(now)
    if start < earliest:
        later_today = [s for s in candidate_starts(day, party_size) if s >= earliest]
        return Problem("in_past", speech.time_passed(later_today[0] if later_today else None))

    return start
