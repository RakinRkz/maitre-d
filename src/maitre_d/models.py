"""Database tables.

The migration in `migrations/versions/` is the authority on the schema; these classes mirror it
for queries. Constraint names are fixed by the naming convention because the booking code tells
"that table was just taken" from "this request was already made" by which constraint failed.
"""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    REAL,
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Identity,
    Index,
    MetaData,
    SmallInteger,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, ExcludeConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

NO_OVERLAP = "bookings_no_overlap"
UNIQUE_IDEMPOTENCY_KEY = "bookings_idempotency_key_key"
UNIQUE_REFERENCE = "bookings_reference_key"


class Base(DeclarativeBase):
    metadata = MetaData(
        naming_convention={
            "pk": "%(table_name)s_pkey",
            "fk": "%(table_name)s_%(column_0_name)s_fkey",
            "uq": "%(table_name)s_%(column_0_name)s_key",
            "ck": "%(table_name)s_%(constraint_name)s",
            "ix": "%(table_name)s_%(column_0_name)s_idx",
        }
    )


class DiningTable(Base):
    __tablename__ = "dining_tables"

    id: Mapped[int] = mapped_column(SmallInteger, primary_key=True, autoincrement=False)
    label: Mapped[str] = mapped_column(Text, unique=True)
    seats: Mapped[int] = mapped_column(SmallInteger)

    __table_args__ = (CheckConstraint("seats > 0", name="seats_positive"),)


class Booking(Base):
    __tablename__ = "bookings"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    reference: Mapped[str] = mapped_column(Text, unique=True)
    table_id: Mapped[int] = mapped_column(SmallInteger, ForeignKey("dining_tables.id"))
    party_size: Mapped[int] = mapped_column(SmallInteger)
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    guest_name: Mapped[str] = mapped_column(Text)
    guest_phone: Mapped[str] = mapped_column(Text, index=True)
    notes: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, server_default="confirmed")
    conversation_id: Mapped[str | None] = mapped_column(Text)
    idempotency_key: Mapped[str | None] = mapped_column(Text, unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        CheckConstraint("party_size BETWEEN 1 AND 8", name="party_size_range"),
        CheckConstraint("ends_at > starts_at", name="ends_after_start"),
        CheckConstraint("status IN ('confirmed', 'cancelled')", name="status_valid"),
        ExcludeConstraint(
            ("table_id", "="),
            (func.tstzrange(text("starts_at"), text("ends_at"), text("'[)'")), "&&"),
            name=NO_OVERLAP,
            using="gist",
            where=text("status = 'confirmed'"),
        ),
    )


class ToolInvocation(Base):
    """One row per request to /tools/*, written by our own server after the response is sent."""

    __tablename__ = "tool_invocations"

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    conversation_id: Mapped[str] = mapped_column(Text)
    tool: Mapped[str] = mapped_column(Text)
    ok: Mapped[bool]
    error: Mapped[str | None] = mapped_column(Text)
    http_status: Mapped[int] = mapped_column(SmallInteger)
    server_ms: Mapped[float] = mapped_column(REAL)
    request: Mapped[dict[str, Any]] = mapped_column(JSONB)
    response: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        Index("tool_invocations_conversation_idx", "conversation_id", "created_at"),
        Index("tool_invocations_tool_idx", "tool", "created_at"),
    )


class CallerMessage(Base):
    __tablename__ = "caller_messages"

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    conversation_id: Mapped[str] = mapped_column(Text)
    caller_name: Mapped[str] = mapped_column(Text)
    caller_phone: Mapped[str] = mapped_column(Text)
    message: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    handled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
