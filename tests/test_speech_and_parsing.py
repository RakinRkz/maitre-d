"""How the agent hears times, and how leniently tool arguments are read.

Parsing is forgiving where a voice transcript is predictably messy and strict where a guess would
book the wrong thing.
"""

from datetime import date, time
from typing import Any

import pytest
from pydantic import ValidationError

from maitre_d import speech
from maitre_d.schemas import ChangeBookingRequest, CheckAvailabilityRequest


@pytest.mark.parametrize(
    ("at", "spoken"),
    [
        (time(20, 0), "8 pm"),
        (time(19, 30), "7:30 pm"),
        (time(12, 0), "midday"),
        (time(12, 45), "12:45 pm"),
        (time(9, 5), "9:05 am"),
    ],
)
def test_times_are_spoken_the_way_people_say_them(at: time, spoken: str) -> None:
    assert speech.spoken_time(at) == spoken


def test_the_year_is_only_said_when_it_is_not_this_year() -> None:
    today = date(2026, 12, 20)
    assert speech.spoken_date(date(2026, 12, 24), today) == "Thursday 24 December"
    assert speech.spoken_date(date(2027, 1, 2), today) == "Saturday 2 January 2027"


def test_lists_read_naturally() -> None:
    assert speech.spoken_list(["7 pm"]) == "7 pm"
    assert speech.spoken_list(["7 pm", "9 pm"]) == "7 pm or 9 pm"
    assert speech.spoken_list(["6 pm", "7 pm", "9 pm"]) == "6 pm, 7 pm or 9 pm"


def availability(**overrides: Any) -> CheckAvailabilityRequest:
    fields = {
        "conversation_id": "conv_1",
        "date": "2026-10-23",
        "weekday": "Friday",
        "time": "20:00",
        "party_size": 4,
    }
    return CheckAvailabilityRequest.model_validate(fields | overrides)


@pytest.mark.parametrize(
    ("said", "parsed"),
    [
        ("20:00", time(20, 0)),
        ("8:30", time(8, 30)),
        ("19:45:00", time(19, 45)),
        ("8pm", time(20, 0)),
        ("8:30 pm", time(20, 30)),
        ("7.15 p.m.", time(19, 15)),
        ("12pm", time(12, 0)),
        ("12am", time(0, 0)),
    ],
)
def test_times_are_read_in_the_forms_a_model_produces(said: str, parsed: time) -> None:
    assert availability(time=said).time == parsed


@pytest.mark.parametrize("said", ["eight", "8", "25:00", "13pm", "around 8"])
def test_times_that_would_need_a_guess_are_refused(said: str) -> None:
    with pytest.raises(ValidationError):
        availability(time=said)


@pytest.mark.parametrize("said", ["30/10/2026", "2026-10-30T20:00", "next Friday", "20261030"])
def test_dates_must_be_iso(said: str) -> None:
    """30/10 vs 10/30 is exactly the ambiguity that books the wrong day, so no guessing."""
    with pytest.raises(ValidationError):
        availability(date=said)


def test_weekdays_are_read_in_any_case() -> None:
    assert availability(weekday=" friday ").weekday == "Friday"


def test_a_spoken_reference_is_read_back_into_shape() -> None:
    request = ChangeBookingRequest(
        conversation_id="conv_1", reference="k7m 2qx", name="Nadia", phone="07700900123"
    )
    assert request.reference == "K7M2QX"


def test_changing_the_date_needs_the_weekday_too() -> None:
    with pytest.raises(ValidationError, match="weekday is required when date is given"):
        ChangeBookingRequest.model_validate(
            {
                "conversation_id": "conv_1",
                "reference": "K7M2QX",
                "name": "Nadia",
                "phone": "07700900123",
                "date": "2026-10-24",
            }
        )


def test_a_weekday_without_a_date_is_rejected() -> None:
    """A lone weekday used to be dropped, so "move it to Saturday" changed nothing."""
    with pytest.raises(ValidationError, match="date is required when weekday is given"):
        ChangeBookingRequest.model_validate(
            {
                "conversation_id": "conv_1",
                "reference": "K7M2QX",
                "name": "Nadia",
                "phone": "07700900123",
                "weekday": "Saturday",
            }
        )
