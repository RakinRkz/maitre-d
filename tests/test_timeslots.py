"""Turning what the caller said into a bookable London time, and every way that can fail.

UK clocks go back at 02:00 BST on the last Sunday of October (25 October 2026) and forward at
01:00 GMT on the last Sunday of March (29 March 2026, 28 March 2027). The restaurant is shut at
those hours, so most clock-change bugs here would be silent until they weren't: a grid built by
adding minutes to a UTC instant, or "today" computed in UTC, is right most of the year.
"""

from datetime import UTC, date, datetime, time, timedelta

import pytest
from helpers import NOW

from maitre_d import timeslots
from maitre_d.timeslots import InvalidLocalTime, Problem


def utc(year: int, month: int, day: int, hour: int = 0, minute: int = 0) -> datetime:
    return datetime(year, month, day, hour, minute, tzinfo=UTC)


def problem(result: datetime | Problem) -> Problem:
    assert isinstance(result, Problem), f"expected a Problem, got {result}"
    return result


def test_an_evening_after_the_clocks_go_back_is_gmt() -> None:
    assert timeslots.local_datetime(date(2026, 10, 25), time(19, 0)) == utc(2026, 10, 25, 19, 0)


def test_an_evening_after_the_clocks_go_forward_is_bst() -> None:
    assert timeslots.local_datetime(date(2026, 3, 29), time(19, 0)) == utc(2026, 3, 29, 18, 0)


def test_a_time_the_clocks_skip_is_rejected() -> None:
    """01:30 on 29 March 2026 never happens in London: 01:00 GMT becomes 02:00 BST."""
    with pytest.raises(InvalidLocalTime):
        timeslots.local_datetime(date(2026, 3, 29), time(1, 30))


def test_a_time_the_clocks_repeat_is_rejected() -> None:
    """01:30 on 25 October 2026 happens twice in London, so it names no single instant."""
    with pytest.raises(InvalidLocalTime):
        timeslots.local_datetime(date(2026, 10, 25), time(1, 30))


def test_the_day_the_clocks_go_back_is_25_hours_long() -> None:
    start, end = timeslots.day_bounds(date(2026, 10, 25))
    assert (start, end - start) == (utc(2026, 10, 24, 23, 0), timedelta(hours=25))


def test_the_day_the_clocks_go_forward_is_23_hours_long() -> None:
    start, end = timeslots.day_bounds(date(2027, 3, 28))
    assert (start, end - start) == (utc(2027, 3, 28, 0, 0), timedelta(hours=23))


@pytest.mark.parametrize("clock_change_day", [date(2026, 10, 25), date(2027, 3, 28)])
def test_the_slot_grid_is_the_same_wall_clock_times_on_a_clock_change_day(
    clock_change_day: date,
) -> None:
    ordinary_sunday = clock_change_day + timedelta(days=7)
    on_change = [s.time() for s in timeslots.candidate_starts(clock_change_day, 4)]
    on_ordinary = [s.time() for s in timeslots.candidate_starts(ordinary_sunday, 4)]
    assert on_change == on_ordinary
    assert on_change[0] == time(12, 0)
    assert on_change[-1] == time(21, 0)


def test_today_is_the_date_in_london_not_in_utc() -> None:
    """At 00:30 BST on 2 July it is still 1 July in UTC."""
    assert timeslots.today_in_london(utc(2026, 7, 1, 23, 30)) == date(2026, 7, 2)


def test_a_weekday_that_disagrees_with_the_date_is_caught() -> None:
    """The caller said Friday; the model worked out the 31st, which is a Saturday."""
    result = problem(timeslots.validate_start(date(2026, 10, 31), "Friday", time(20), 4, NOW))
    assert result.code == "weekday_mismatch"
    assert result.say == "Saturday 31 October is not a Friday. Did you mean Friday 30 October?"


def test_the_suggested_weekday_is_never_in_the_past() -> None:
    """Wednesday 21 Oct with "Monday": the nearest Monday is the 19th, which has gone."""
    result = problem(timeslots.validate_start(date(2026, 10, 21), "Monday", time(20), 4, NOW))
    assert "Monday 26 October" in result.say


def test_eight_in_the_morning_is_offered_back_as_eight_in_the_evening() -> None:
    result = problem(timeslots.validate_start(date(2026, 10, 23), "Friday", time(8), 4, NOW))
    assert result.code == "outside_hours"
    assert result.say == "We're not open then. Did you mean 8 pm?"


def test_a_time_outside_hours_gets_the_hours_and_the_last_table() -> None:
    result = problem(timeslots.validate_start(date(2026, 10, 23), "Friday", time(16), 4, NOW))
    assert result.say == (
        "On Fridays we serve lunch from midday to 3 pm and dinner from 5:30 pm to 10:30 pm. "
        "The last tables for 4 are at 1:30 pm and 9 pm."
    )


def test_a_bigger_party_has_an_earlier_last_table_because_it_sits_longer() -> None:
    friday = date(2026, 10, 23)
    assert isinstance(timeslots.validate_start(friday, "Friday", time(21), 4, NOW), datetime)
    assert problem(timeslots.validate_start(friday, "Friday", time(21), 6, NOW)).code == (
        "outside_hours"
    )
    assert isinstance(timeslots.validate_start(friday, "Friday", time(20, 30), 6, NOW), datetime)


def test_mondays_are_closed() -> None:
    result = problem(timeslots.validate_start(date(2026, 10, 26), "Monday", time(20), 2, NOW))
    assert result.code == "closed"
    assert result.say == "We're closed on Mondays. We're open Tuesday to Sunday."


def test_christmas_day_is_closed_whatever_the_weekday() -> None:
    december = utc(2026, 12, 1, 10, 0)
    result = problem(timeslots.validate_start(date(2026, 12, 25), "Friday", time(20), 2, december))
    assert result.say == "We're closed on Friday 25 December."


def test_a_time_off_the_quarter_hour_offers_the_neighbouring_ones() -> None:
    result = problem(timeslots.validate_start(date(2026, 10, 23), "Friday", time(20, 10), 2, NOW))
    assert result.code == "invalid_time"
    assert result.say == "We book on the quarter hour. Would 8 pm or 8:15 pm suit?"


def test_a_date_that_has_passed_is_refused() -> None:
    result = problem(timeslots.validate_start(date(2026, 10, 18), "Sunday", time(20), 2, NOW))
    assert result.code == "in_past"


def test_too_soon_today_offers_the_next_sitting() -> None:
    """At 12:10 with 15 minutes' notice, 12:15 is too soon and 12:30 is the next slot."""
    lunchtime = utc(2026, 10, 20, 11, 10)
    result = problem(
        timeslots.validate_start(date(2026, 10, 20), "Tuesday", time(12, 15), 2, lunchtime)
    )
    assert result.code == "in_past"
    assert result.say == "That's too soon. The earliest I can book today is 12:30 pm."


def test_bookings_open_sixty_days_ahead_and_no_further() -> None:
    assert isinstance(
        timeslots.validate_start(date(2026, 12, 19), "Saturday", time(20), 2, NOW), datetime
    )
    result = problem(timeslots.validate_start(date(2026, 12, 20), "Sunday", time(20), 2, NOW))
    assert result.code == "too_far_ahead"
    assert result.say == "We take bookings up to Saturday 19 December."


def test_parties_above_the_largest_table_go_to_the_events_team() -> None:
    result = problem(timeslots.validate_start(date(2026, 10, 23), "Friday", time(20), 9, NOW))
    assert result.code == "party_too_large"
    assert "events team" in result.say


def test_a_valid_request_comes_back_as_an_aware_london_time() -> None:
    start = timeslots.validate_start(date(2026, 10, 25), "Sunday", time(19), 2, NOW)
    assert start == utc(2026, 10, 25, 19, 0)
    assert isinstance(start, datetime) and start.tzinfo is not None
