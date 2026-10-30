"""Booking domain: tables, bookings with the no-overlap guarantee, tool invocations, messages.

Revision ID: 0001
Revises:
Create Date: 2026-10-25
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # btree_gist lets a plain integer (table_id) share a GiST index with a range, which the
    # exclusion constraint below needs. It is a trusted extension, so the database owner can
    # create it without superuser rights.
    op.execute("CREATE EXTENSION IF NOT EXISTS btree_gist")

    op.create_table(
        "dining_tables",
        sa.Column("id", sa.SmallInteger(), autoincrement=False, nullable=False),
        sa.Column("label", sa.Text(), nullable=False),
        sa.Column("seats", sa.SmallInteger(), nullable=False),
        sa.CheckConstraint("seats > 0", name="dining_tables_seats_positive"),
        sa.PrimaryKeyConstraint("id", name="dining_tables_pkey"),
        sa.UniqueConstraint("label", name="dining_tables_label_key"),
    )

    op.create_table(
        "bookings",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("reference", sa.Text(), nullable=False),
        sa.Column("table_id", sa.SmallInteger(), nullable=False),
        sa.Column("party_size", sa.SmallInteger(), nullable=False),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ends_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("guest_name", sa.Text(), nullable=False),
        sa.Column("guest_phone", sa.Text(), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("status", sa.Text(), server_default="confirmed", nullable=False),
        sa.Column("conversation_id", sa.Text(), nullable=True),
        sa.Column("idempotency_key", sa.Text(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("party_size BETWEEN 1 AND 8", name="bookings_party_size_range"),
        sa.CheckConstraint("ends_at > starts_at", name="bookings_ends_after_start"),
        sa.CheckConstraint("status IN ('confirmed', 'cancelled')", name="bookings_status_valid"),
        sa.ForeignKeyConstraint(["table_id"], ["dining_tables.id"], name="bookings_table_id_fkey"),
        sa.PrimaryKeyConstraint("id", name="bookings_pkey"),
        sa.UniqueConstraint("reference", name="bookings_reference_key"),
        sa.UniqueConstraint("idempotency_key", name="bookings_idempotency_key_key"),
    )
    # The guarantee that no table is ever double-booked, whatever code path writes the row.
    # Half-open ranges let a sitting ending at 20:00 share a table with one starting at 20:00;
    # the WHERE clause means a cancelled booking frees its table without being deleted.
    op.execute(
        """
        ALTER TABLE bookings ADD CONSTRAINT bookings_no_overlap
        EXCLUDE USING gist (
            table_id WITH =,
            tstzrange(starts_at, ends_at, '[)') WITH &&
        ) WHERE (status = 'confirmed')
        """
    )
    op.create_index("bookings_starts_at_idx", "bookings", ["starts_at"])
    op.create_index("bookings_guest_phone_idx", "bookings", ["guest_phone"])

    op.create_table(
        "tool_invocations",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("conversation_id", sa.Text(), nullable=False),
        sa.Column("tool", sa.Text(), nullable=False),
        sa.Column("ok", sa.Boolean(), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("http_status", sa.SmallInteger(), nullable=False),
        sa.Column("server_ms", sa.REAL(), nullable=False),
        sa.Column("request", postgresql.JSONB(), nullable=False),
        sa.Column("response", postgresql.JSONB(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("id", name="tool_invocations_pkey"),
    )
    op.create_index(
        "tool_invocations_conversation_idx", "tool_invocations", ["conversation_id", "created_at"]
    )
    op.create_index("tool_invocations_tool_idx", "tool_invocations", ["tool", "created_at"])

    op.create_table(
        "caller_messages",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("conversation_id", sa.Text(), nullable=False),
        sa.Column("caller_name", sa.Text(), nullable=False),
        sa.Column("caller_phone", sa.Text(), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("handled_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id", name="caller_messages_pkey"),
    )


def downgrade() -> None:
    op.drop_table("caller_messages")
    op.drop_table("tool_invocations")
    op.drop_table("bookings")
    op.drop_table("dining_tables")
    op.execute("DROP EXTENSION IF EXISTS btree_gist")
