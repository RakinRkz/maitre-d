"""Every caller-facing sentence a tool hands the agent to say.

Tool replies carry a `say` field that the agent reads aloud as it is, so the wording lives in one
module where it can be reviewed and tested. Sentences are short, address the caller directly, use
the restaurant's local time, and spell out the weekday and date so the caller can catch a
misunderstanding.

Model guidance is not speech: the field hints that tell the model how to fix a malformed call live
in `routes/tools.py` and travel in `ToolReply.detail`.

The month and weekday names are spelled out here rather than taken from strftime, whose output
depends on the server's locale.
"""

from collections.abc import Sequence
from datetime import date, datetime, time

from maitre_d import restaurant

WEEKDAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
MONTHS = (
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
)


def spoken_time(at: time) -> str:
    if at == time(12, 0):
        return "midday"
    hour = at.hour % 12 or 12
    suffix = "am" if at.hour < 12 else "pm"
    if at.minute == 0:
        return f"{hour} {suffix}"
    return f"{hour}:{at.minute:02d} {suffix}"


def spoken_date(day: date, today: date) -> str:
    text = f"{WEEKDAYS[day.weekday()]} {day.day} {MONTHS[day.month - 1]}"
    if day.year != today.year:
        text += f" {day.year}"
    return text


def spoken_slot(start: datetime, today: date) -> str:
    return f"{spoken_date(start.date(), today)} at {spoken_time(start.time())}"


def spoken_list(items: Sequence[str], conjunction: str = "or") -> str:
    if len(items) <= 1:
        return "".join(items)
    return f"{', '.join(items[:-1])} {conjunction} {items[-1]}"


def _table_for(party_size: int) -> str:
    return f"a table for {party_size}"


def _offer(starts: Sequence[datetime], requested: datetime, today: date) -> str:
    """Name alternatives as briefly as is unambiguous: times alone if they share the day asked."""
    if all(s.date() == requested.date() for s in starts):
        times = [spoken_time(s.time()) for s in starts]
        return f"I can offer {spoken_list(times)} that day."
    if all(s.time() == requested.time() for s in starts):
        days = [spoken_date(s.date(), today) for s in starts]
        return f"{spoken_time(requested.time())} is free on {spoken_list(days)}."
    return f"I can offer {spoken_list([spoken_slot(s, today) for s in starts])}."


# Availability


def available(start: datetime, party_size: int, today: date) -> str:
    return f"Yes, we have {_table_for(party_size)} on {spoken_slot(start, today)}."


def fully_booked(
    start: datetime, party_size: int, alternatives: Sequence[datetime], today: date
) -> str:
    full = f"{spoken_slot(start, today)} is fully booked for {party_size}."
    if not alternatives:
        return (
            f"{full} I can't find a free table near that time this week. "
            "I can take a message for the team."
        )
    return f"{full} {_offer(alternatives, start, today)}"


def slot_taken(
    start: datetime, party_size: int, alternatives: Sequence[datetime], today: date
) -> str:
    gone = f"Sorry, the last table for {party_size} on {spoken_slot(start, today)} has just gone."
    if not alternatives:
        return f"{gone} I can't find another free table near that time this week."
    return f"{gone} {_offer(alternatives, start, today)}"


# Problems with the requested date or time


def weekday_mismatch(day: date, said: str, suggestion: date, today: date) -> str:
    return (
        f"{spoken_date(day, today)} is not a {said}. Did you mean {spoken_date(suggestion, today)}?"
    )


def date_passed() -> str:
    return "That date has already passed."


def time_passed(next_start: datetime | None) -> str:
    if next_start is None:
        return "That's too soon, and there are no more tables I can book today."
    return f"That's too soon. The earliest I can book today is {spoken_time(next_start.time())}."


def too_far_ahead(last_day: date, today: date) -> str:
    return f"We take bookings up to {spoken_date(last_day, today)}."


def closed(day: date, today: date) -> str:
    if restaurant.SERVICES_BY_WEEKDAY[day.weekday()]:
        return f"We're closed on {spoken_date(day, today)}."
    open_days = [WEEKDAYS[d] for d, services in restaurant.SERVICES_BY_WEEKDAY.items() if services]
    return (
        f"We're closed on {WEEKDAYS[day.weekday()]}s. We're open {open_days[0]} to {open_days[-1]}."
    )


def outside_hours(day: date, party_size: int, pm_guess: datetime | None) -> str:
    if pm_guess is not None:
        return f"We're not open then. Did you mean {spoken_time(pm_guess.time())}?"
    services = restaurant.services_on(day)
    sitting = restaurant.sitting_length(party_size)
    hours = [
        f"{service.name} from {spoken_time(service.opens)} to {spoken_time(service.closes)}"
        for service in services
    ]
    last_starts = [
        spoken_time((datetime.combine(day, service.closes) - sitting).time())
        for service in services
    ]
    return (
        f"On {WEEKDAYS[day.weekday()]}s we serve {spoken_list(hours, 'and')}. "
        f"The last {'table' if len(last_starts) == 1 else 'tables'} for {party_size} "
        f"{'is' if len(last_starts) == 1 else 'are'} at {spoken_list(last_starts, 'and')}."
    )


def not_on_quarter_hour(options: Sequence[time]) -> str:
    times = [spoken_time(t) for t in options]
    return f"We book on the quarter hour. Would {spoken_list(times)} suit?"


def clocks_change() -> str:
    return "That time doesn't exist or happens twice when the clocks change. Please pick another."


def party_too_large() -> str:
    return (
        f"For more than {restaurant.MAX_PARTY_SIZE} guests our events team handles the booking. "
        "I can take a message and they'll call back."
    )


def invalid_phone() -> str:
    return "That phone number doesn't look complete. Could you give it again?"


# Bookings


def booking_summary(start: datetime, party_size: int, today: date) -> str:
    return f"{_table_for(party_size)} on {spoken_slot(start, today)}"


def booked(start: datetime, party_size: int, name: str, reference: str, today: date) -> str:
    return (
        f"Booked: {booking_summary(start, party_size, today)} under {name}. "
        f"The reference is {reference}."
    )


def already_booked_this_call(start: datetime, party_size: int, today: date) -> str:
    return (
        f"I've already booked you {booking_summary(start, party_size, today)}. "
        "Would you like me to change that booking instead of making a second one?"
    )


def not_found() -> str:
    return (
        "I couldn't find a booking under that name and phone number. "
        "Could you check the spelling, or the number the booking was made with?"
    )


def found(summaries: Sequence[str]) -> str:
    if len(summaries) == 1:
        return f"I found one booking: {summaries[0]}."
    return f"I found {len(summaries)} bookings: {spoken_list(summaries, 'and')}."


def changed(start: datetime, party_size: int, today: date) -> str:
    return f"Done. The booking is now {booking_summary(start, party_size, today)}."


def unchanged(start: datetime, party_size: int, today: date) -> str:
    summary = booking_summary(start, party_size, today)
    return f"It's already booked: {summary}. Nothing needed changing."


def cancelled(start: datetime, party_size: int, today: date) -> str:
    return f"Cancelled: {booking_summary(start, party_size, today)}."


def already_cancelled() -> str:
    return "That booking was already cancelled."


def message_taken() -> str:
    return "I've passed the message to the team. Someone will call back on the number given."


def invalid_request() -> str:
    # Deliberately generic: the field hints are for the model, not for the caller to hear.
    return "Sorry, I didn't catch that. Could you say it again?"
