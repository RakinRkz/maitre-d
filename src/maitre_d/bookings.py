"""Availability, alternatives, and the logic behind each booking tool.

Correctness under concurrency does not come from this module. The availability check here picks a
table and finds good alternatives, but two requests can both pass it; the `bookings_no_overlap`
exclusion constraint decides which one commits, and the loser moves on to the next table. Each
insert runs in a savepoint so a lost race costs one statement, not the whole request.

Every reply carries `say`, a sentence the agent can read out, built in `speech`.
"""

import hashlib
import logging
import secrets
import unicodedata
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any

import phonenumbers
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from maitre_d import restaurant, speech, timeslots
from maitre_d.models import (
    NO_OVERLAP,
    UNIQUE_IDEMPOTENCY_KEY,
    UNIQUE_REFERENCE,
    Booking,
    CallerMessage,
)
from maitre_d.restaurant import TIMEZONE, DiningTable
from maitre_d.schemas import (
    BookingOut,
    CancelBookingRequest,
    ChangeBookingRequest,
    CheckAvailabilityRequest,
    CreateBookingRequest,
    FindBookingRequest,
    SlotOut,
    TakeMessageRequest,
    ToolReply,
)
from maitre_d.timeslots import Problem

log = logging.getLogger(__name__)

# No I, L, O, 0 or 1: they are the characters people mishear and misread.
REFERENCE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
REFERENCE_LENGTH = 6


def new_reference() -> str:
    return "".join(secrets.choice(REFERENCE_ALPHABET) for _ in range(REFERENCE_LENGTH))


def normalise_phone(raw: str) -> str | None:
    """E.164, reading numbers without a country code as UK numbers. None if it can't be one.

    "Possible" (right length for the country) rather than "valid" (in an allocated range): a
    real caller on a newly allocated range shouldn't be turned away because the library's
    metadata is behind, and the 07700 900xxx range used for fictional data is deliberately
    unallocated. Local-only lengths are refused, since "0770" is a fragment, not a number.
    """
    try:
        parsed = phonenumbers.parse(raw, "GB")
    except phonenumbers.NumberParseException:
        return None
    reason = phonenumbers.is_possible_number_with_reason(parsed)
    if reason != phonenumbers.ValidationResult.IS_POSSIBLE:
        return None
    return phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.E164)


TITLES = frozenset({"mr", "mrs", "ms", "miss", "mx", "dr", "prof", "sir", "madam"})


def name_tokens(name: str) -> list[str]:
    """Casefolded words, no accents, punctuation or titles: "Mrs Zoë O'Brien" -> zoe, o, brien."""
    decomposed = unicodedata.normalize("NFKD", name)
    without_accents = "".join(c for c in decomposed if not unicodedata.combining(c))
    words = "".join(c if c.isalnum() else " " for c in without_accents.casefold()).split()
    return [w for w in words if w not in TITLES]


def names_match(given: str, on_booking: str) -> bool:
    """Every word the caller gives must be in the booking's name.

    "Ahmed", "Nadia" and "Mrs Ahmed" all match a booking under "Nadia Ahmed"; "Nadia Khan" does
    not. The phone number is the stronger check; the name stops someone who only knows a number
    from hearing whatever is booked against it.
    """
    given_tokens = set(name_tokens(given))
    return bool(given_tokens) and given_tokens <= set(name_tokens(on_booking))


def idempotency_key(
    conversation_id: str, start: datetime, party_size: int, name: str, phone: str
) -> str:
    """The same booking asked for twice in one call gets the same key.

    The conversation ID comes from the platform, not the model, so a retry or a repeated tool
    call carries the same one. Notes are left out: a model retrying may reword them.
    """
    parts = [
        conversation_id,
        start.date().isoformat(),
        f"{start:%H:%M}",
        str(party_size),
        " ".join(name_tokens(name)),
        phone,
    ]
    return hashlib.sha256("\x1f".join(parts).encode()).hexdigest()


@dataclass(frozen=True)
class Occupied:
    table_id: int
    starts_at: datetime
    ends_at: datetime


async def load_occupied(
    session: AsyncSession, first_day: date, last_day: date, *, exclude: uuid.UUID | None = None
) -> list[Occupied]:
    """Confirmed bookings touching the given local days, in one query."""
    start, _ = timeslots.day_bounds(first_day)
    _, end = timeslots.day_bounds(last_day)
    query = select(Booking.table_id, Booking.starts_at, Booking.ends_at).where(
        Booking.status == "confirmed", Booking.starts_at < end, Booking.ends_at > start
    )
    if exclude is not None:
        query = query.where(Booking.id != exclude)
    rows = await session.execute(query)
    return [Occupied(row.table_id, row.starts_at, row.ends_at) for row in rows]


def free_tables(occupied: list[Occupied], start: datetime, party_size: int) -> list[DiningTable]:
    """Tables that seat the party and are free for the whole sitting, best fit first."""
    end = start + restaurant.sitting_length(party_size)
    busy = {o.table_id for o in occupied if o.starts_at < end and start < o.ends_at}
    return [t for t in restaurant.tables_for(party_size) if t.id not in busy]


async def find_alternatives(
    session: AsyncSession,
    requested: datetime,
    party_size: int,
    now: datetime,
    *,
    occupied: list[Occupied] | None = None,
    exclude: uuid.UUID | None = None,
) -> list[datetime]:
    """The nearest free starts on the same day; failing that, the same time on later days."""
    day = requested.date()
    if occupied is None:
        occupied = await load_occupied(session, day, day, exclude=exclude)
    earliest = timeslots.earliest_bookable(now)
    same_day = [
        s
        for s in timeslots.candidate_starts(day, party_size)
        if s != requested
        and abs(s - requested) <= restaurant.ALTERNATIVE_WINDOW
        and s >= earliest
        and free_tables(occupied, s, party_size)
    ]
    if same_day:
        same_day.sort(key=lambda s: (abs(s - requested), s))
        return same_day[: restaurant.MAX_ALTERNATIVES]

    today = timeslots.today_in_london(now)
    first = day + timedelta(days=1)
    last = min(
        day + timedelta(days=restaurant.LATER_DAYS_TO_SEARCH), today + restaurant.BOOKING_HORIZON
    )
    if first > last:
        return []
    later_occupied = await load_occupied(session, first, last, exclude=exclude)
    later: list[datetime] = []
    candidate_day = first
    while candidate_day <= last and len(later) < restaurant.MAX_ALTERNATIVES:
        starts = {s.time(): s for s in timeslots.candidate_starts(candidate_day, party_size)}
        start = starts.get(requested.time())
        if start is not None and free_tables(later_occupied, start, party_size):
            later.append(start)
        candidate_day += timedelta(days=1)
    return later


# Building replies


def _slot_out(start: datetime, today: date) -> SlotOut:
    return SlotOut(
        date=start.date(), time=f"{start:%H:%M}", spoken=speech.spoken_slot(start, today)
    )


def _local_start(booking: Booking) -> datetime:
    return booking.starts_at.astimezone(TIMEZONE)


def _booking_out(booking: Booking, today: date) -> BookingOut:
    start = _local_start(booking)
    return BookingOut(
        reference=booking.reference,
        date=start.date(),
        time=f"{start:%H:%M}",
        party_size=booking.party_size,
        name=booking.guest_name,
        status="cancelled" if booking.status == "cancelled" else "confirmed",
        spoken=speech.booking_summary(start, booking.party_size, today),
    )


def _problem_reply(problem: Problem) -> ToolReply:
    return ToolReply(ok=False, error=problem.code, say=problem.say)


def _booked_reply(booking: Booking, today: date) -> ToolReply:
    return ToolReply(
        ok=True,
        booking=_booking_out(booking, today),
        say=speech.booked(
            _local_start(booking), booking.party_size, booking.guest_name, booking.reference, today
        ),
    )


def _not_found() -> ToolReply:
    return ToolReply(ok=False, error="not_found", say=speech.not_found())


def _invalid_phone() -> ToolReply:
    return ToolReply(ok=False, error="invalid_phone", say=speech.invalid_phone())


# Writes


# Arbitrary first key for pg_advisory_xact_lock, so these locks can't collide with any other use.
DAY_LOCK_NAMESPACE = 0x4D44


async def _queue_for_day(session: AsyncSession, day: date) -> None:
    """Wait for any other booking write on this local day to finish; hold the turn until commit.

    This is about speed, not correctness. Postgres checks an exclusion constraint after the new row
    is already in the index, so two transactions inserting clashing rows at the same instant can
    each find the other's row and wait for it (execIndexing.c: "There is a chance of deadlock, if
    two backends insert a tuple at the same time"). The deadlock detector settles it, but only
    after deadlock_timeout (1 s by default), and in a pile-up the cycles re-form: the race test
    took 47 s with twelve callers. Queued, each writer sees the previous one's committed row and
    gets an immediate, clean exclusion violation.

    Availability is deliberately not re-read after the lock is taken. Which caller gets a table is
    still decided by the constraint alone, and the race test without it still double-books.
    """
    await session.execute(select(func.pg_advisory_xact_lock(DAY_LOCK_NAMESPACE, day.toordinal())))


def _refused_by(err: IntegrityError) -> str | None:
    # SQLAlchemy wraps asyncpg's exception; asyncpg's original names the constraint.
    return getattr(getattr(err.orig, "__cause__", None), "constraint_name", None)


async def _insert_booking(session: AsyncSession, values: dict[str, Any]) -> Booking | str:
    """Insert in a savepoint. Returns the booking, or the constraint that refused it.

    A reference collision (about one in 900 million per pair) is retried with a new reference. Any
    other integrity error is a bug and propagates.
    """
    for _ in range(3):
        booking = Booking(**values, reference=new_reference())
        try:
            async with session.begin_nested():
                session.add(booking)
        except IntegrityError as err:
            refused_by = _refused_by(err)
            if refused_by == UNIQUE_REFERENCE:
                continue
            if refused_by in (NO_OVERLAP, UNIQUE_IDEMPOTENCY_KEY):
                return refused_by
            raise
        return booking
    raise RuntimeError("could not generate an unused booking reference")


async def _move_booking(
    session: AsyncSession, booking_id: uuid.UUID, values: dict[str, Any]
) -> str | None:
    """Update in a savepoint. Returns None, or NO_OVERLAP if the new slot clashes."""
    try:
        async with session.begin_nested():
            await session.execute(update(Booking).where(Booking.id == booking_id).values(**values))
    except IntegrityError as err:
        if _refused_by(err) == NO_OVERLAP:
            return NO_OVERLAP
        raise
    return None


async def _booked_earlier_in_call(
    session: AsyncSession, conversation_id: str, phone: str, day: date
) -> Booking | None:
    start, end = timeslots.day_bounds(day)
    result = await session.scalars(
        select(Booking)
        .where(
            Booking.conversation_id == conversation_id,
            Booking.guest_phone == phone,
            Booking.status == "confirmed",
            Booking.starts_at >= start,
            Booking.starts_at < end,
        )
        .limit(1)
    )
    return result.first()


async def _verified_booking(
    session: AsyncSession, reference: str, name: str, phone: str, now: datetime
) -> Booking | None:
    """The upcoming booking with this reference, only if the name and phone both match it.

    Any mismatch returns None, the same as no booking at all, so a caller can't learn whether a
    reference or a number is in the book by trying it.
    """
    normalised = normalise_phone(phone)
    if normalised is None:
        return None
    booking = await session.scalar(select(Booking).where(Booking.reference == reference))
    if (
        booking is None
        or booking.guest_phone != normalised
        or not names_match(name, booking.guest_name)
        or booking.ends_at <= now
    ):
        return None
    return booking


# The tools


async def check_availability(
    session: AsyncSession, request: CheckAvailabilityRequest, now: datetime
) -> ToolReply:
    today = timeslots.today_in_london(now)
    start = timeslots.validate_start(
        request.date, request.weekday, request.time, request.party_size, now
    )
    if isinstance(start, Problem):
        return _problem_reply(start)

    occupied = await load_occupied(session, start.date(), start.date())
    if free_tables(occupied, start, request.party_size):
        return ToolReply(
            ok=True,
            available=True,
            slot=_slot_out(start, today),
            say=speech.available(start, request.party_size, today),
        )
    alternatives = await find_alternatives(
        session, start, request.party_size, now, occupied=occupied
    )
    return ToolReply(
        ok=True,
        available=False,
        alternatives=[_slot_out(a, today) for a in alternatives],
        say=speech.fully_booked(start, request.party_size, alternatives, today),
    )


async def create_booking(
    session: AsyncSession, request: CreateBookingRequest, now: datetime
) -> ToolReply:
    today = timeslots.today_in_london(now)
    start = timeslots.validate_start(
        request.date, request.weekday, request.time, request.party_size, now
    )
    if isinstance(start, Problem):
        return _problem_reply(start)
    phone = normalise_phone(request.phone)
    if phone is None:
        return _invalid_phone()

    key = idempotency_key(request.conversation_id, start, request.party_size, request.name, phone)
    # The model called twice, or the platform retried after a timeout: answer as the first time.
    existing = await session.scalar(select(Booking).where(Booking.idempotency_key == key))
    if existing is not None:
        if (
            existing.status == "confirmed"
            and existing.starts_at == start
            and existing.party_size == request.party_size
        ):
            return _booked_reply(existing, today)
        # The key is kept when a booking is moved (see change_booking): that stops a late retry of
        # the original create from booking the old slot a second time. So a key hit can point at a
        # booking that no longer matches this request. Report the booking as it stands rather than
        # a false "Booked" for the old slot. Cancelling clears the key, so a cancelled row should
        # not reach this branch.
        return ToolReply(
            ok=False,
            error="already_booked_this_call",
            booking=_booking_out(existing, today),
            say=speech.already_booked_this_call(_local_start(existing), existing.party_size, today),
        )

    # A different time on the same day in the same call is almost always the caller correcting
    # themselves, which the model has mistaken for a second booking. Best effort: two such calls
    # racing each other could both pass, which is acceptable because both would be real bookings.
    earlier = await _booked_earlier_in_call(session, request.conversation_id, phone, start.date())
    if earlier is not None:
        return ToolReply(
            ok=False,
            error="already_booked_this_call",
            booking=_booking_out(earlier, today),
            say=speech.already_booked_this_call(_local_start(earlier), earlier.party_size, today),
        )

    values: dict[str, Any] = {
        "status": "confirmed",
        "party_size": request.party_size,
        "starts_at": start,
        "ends_at": start + restaurant.sitting_length(request.party_size),
        "guest_name": request.name,
        "guest_phone": phone,
        "notes": request.notes,
        "conversation_id": request.conversation_id,
        "idempotency_key": key,
    }
    occupied = await load_occupied(session, start.date(), start.date())
    await _queue_for_day(session, start.date())
    candidates = free_tables(occupied, start, request.party_size)
    for table in candidates:
        outcome = await _insert_booking(session, {**values, "table_id": table.id})
        if isinstance(outcome, Booking):
            await session.commit()
            log.info("booked %s on %s", outcome.reference, table.label)
            return _booked_reply(outcome, today)
        if outcome == UNIQUE_IDEMPOTENCY_KEY:
            # The same request, sent concurrently, committed first. Every insert carries the key,
            # so this is the one place a duplicate is stopped even after it chose another table.
            winner = await session.scalar(select(Booking).where(Booking.idempotency_key == key))
            await session.commit()
            if winner is None:
                raise RuntimeError("idempotency key conflict with no committed booking")
            return _booked_reply(winner, today)
        # NO_OVERLAP: another caller took this table between our read and our write.
        log.info("lost the race for %s at %s; trying the next table", table.label, start)

    await session.rollback()
    alternatives = await find_alternatives(session, start, request.party_size, now)
    # "Has just gone" only if we actually lost a race; if it was full when we looked, say so.
    say = speech.slot_taken if candidates else speech.fully_booked
    return ToolReply(
        ok=False,
        error="slot_taken",
        alternatives=[_slot_out(a, today) for a in alternatives],
        say=say(start, request.party_size, alternatives, today),
    )


async def find_booking(
    session: AsyncSession, request: FindBookingRequest, now: datetime
) -> ToolReply:
    today = timeslots.today_in_london(now)
    phone = normalise_phone(request.phone)
    if phone is None:
        return _invalid_phone()
    candidates = await session.scalars(
        select(Booking)
        .where(Booking.guest_phone == phone, Booking.status == "confirmed", Booking.ends_at > now)
        .order_by(Booking.starts_at)
    )
    matches = [
        _booking_out(b, today) for b in candidates if names_match(request.name, b.guest_name)
    ]
    if not matches:
        return _not_found()
    return ToolReply(ok=True, bookings=matches, say=speech.found([m.spoken for m in matches]))


async def change_booking(
    session: AsyncSession, request: ChangeBookingRequest, now: datetime
) -> ToolReply:
    today = timeslots.today_in_london(now)
    # Validate the number before the lookup, so a fragment gets "give it again" rather than the
    # not-found sentence that would suggest the booking is missing. The name+phone check is
    # unchanged.
    phone = normalise_phone(request.phone)
    if phone is None:
        return _invalid_phone()
    booking = await _verified_booking(session, request.reference, request.name, phone, now)
    if booking is None or booking.status != "confirmed":
        return _not_found()

    booking_id, current_table = booking.id, booking.table_id
    current = _local_start(booking)
    party_size = request.party_size or booking.party_size
    start = timeslots.validate_start(
        request.date or current.date(),
        request.weekday if request.date else None,
        request.time or current.time(),
        party_size,
        now,
    )
    if isinstance(start, Problem):
        return _problem_reply(start)
    if start == current and party_size == booking.party_size:
        return ToolReply(
            ok=True,
            booking=_booking_out(booking, today),
            say=speech.unchanged(start, party_size, today),
        )

    # The idempotency key is deliberately left alone when the booking moves: a late retry of the
    # original create must not book the old slot a second time. create_booking treats a key hit
    # that no longer matches the request as "already booked this call", with the moved booking.
    occupied = await load_occupied(session, start.date(), start.date(), exclude=booking_id)
    await _queue_for_day(session, start.date())
    # Keep the guest at their table when it still works; otherwise best fit.
    tables = sorted(free_tables(occupied, start, party_size), key=lambda t: t.id != current_table)
    for table in tables:
        refused_by = await _move_booking(
            session,
            booking_id,
            {
                "table_id": table.id,
                "starts_at": start,
                "ends_at": start + restaurant.sitting_length(party_size),
                "party_size": party_size,
            },
        )
        if refused_by == NO_OVERLAP:
            continue
        await session.commit()
        changed = await session.get(Booking, booking_id, populate_existing=True)
        if changed is None:
            raise RuntimeError("booking vanished during change")
        return ToolReply(
            ok=True,
            booking=_booking_out(changed, today),
            say=speech.changed(start, party_size, today),
        )

    await session.rollback()
    alternatives = await find_alternatives(session, start, party_size, now, exclude=booking_id)
    return ToolReply(
        ok=False,
        error="slot_taken",
        alternatives=[_slot_out(a, today) for a in alternatives],
        say=speech.fully_booked(start, party_size, alternatives, today),
    )


async def cancel_booking(
    session: AsyncSession, request: CancelBookingRequest, now: datetime
) -> ToolReply:
    today = timeslots.today_in_london(now)
    # Same as change_booking: a fragment is a phone problem, not a missing booking.
    phone = normalise_phone(request.phone)
    if phone is None:
        return _invalid_phone()
    booking = await _verified_booking(session, request.reference, request.name, phone, now)
    if booking is None:
        return _not_found()
    if booking.status == "cancelled":
        return ToolReply(
            ok=True, booking=_booking_out(booking, today), say=speech.already_cancelled()
        )

    booking_id = booking.id
    # Clearing the idempotency key lets the caller book the same slot again in the same call.
    # A late retry of the original create could then book it twice, but tool retries arrive
    # within seconds of the original, long before a caller gets round to cancelling.
    await session.execute(
        update(Booking)
        .where(Booking.id == booking_id, Booking.status == "confirmed")
        .values(status="cancelled", cancelled_at=now, idempotency_key=None)
    )
    await session.commit()
    cancelled = await session.get(Booking, booking_id, populate_existing=True)
    if cancelled is None:
        raise RuntimeError("booking vanished during cancel")
    return ToolReply(
        ok=True,
        booking=_booking_out(cancelled, today),
        say=speech.cancelled(_local_start(cancelled), cancelled.party_size, today),
    )


async def take_message(
    session: AsyncSession, request: TakeMessageRequest, now: datetime
) -> ToolReply:
    # Keep the number as said if it won't parse: a message with a garbled number is still
    # worth more to staff than no message.
    phone = normalise_phone(request.phone) or request.phone
    duplicate = await session.scalar(
        select(CallerMessage.id).where(
            CallerMessage.conversation_id == request.conversation_id,
            CallerMessage.message == request.message,
        )
    )
    if duplicate is None:
        session.add(
            CallerMessage(
                conversation_id=request.conversation_id,
                caller_name=request.name,
                caller_phone=phone,
                message=request.message,
            )
        )
        await session.commit()
    return ToolReply(ok=True, say=speech.message_taken())
