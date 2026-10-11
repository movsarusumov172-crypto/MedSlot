"""Add patient profiles, booking details, and persistent FSM storage."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261011_0002"
down_revision: str | None = "20261011_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _columns(table_name: str) -> set[str]:
    return {column["name"] for column in sa.inspect(op.get_bind()).get_columns(table_name)}


def upgrade() -> None:
    """Add new fields in place, preserving all existing appointment rows."""
    appointment_columns = _columns("appointments")
    if "visit_reason" not in appointment_columns:
        op.add_column("appointments", sa.Column("visit_reason", sa.String(200), nullable=True))
    if "confirmed" not in appointment_columns:
        op.add_column(
            "appointments",
            sa.Column("confirmed", sa.Boolean(), nullable=False, server_default=sa.false()),
        )
    if "attendance_status" not in appointment_columns:
        op.add_column(
            "appointments",
            sa.Column(
                "attendance_status",
                sa.String(16),
                nullable=False,
                server_default="unmarked",
            ),
        )

    tables = set(sa.inspect(op.get_bind()).get_table_names())
    if "patient_profiles" not in tables:
        op.create_table(
            "patient_profiles",
            sa.Column("telegram_id", sa.BigInteger(), primary_key=True),
            sa.Column("full_name", sa.String(200), nullable=False),
            sa.Column("phone", sa.String(16), nullable=False),
            sa.Column("consented_at", sa.DateTime(timezone=True), nullable=False),
        )
    if "fsm_state_records" not in tables:
        op.create_table(
            "fsm_state_records",
            sa.Column("bot_id", sa.BigInteger(), primary_key=True),
            sa.Column("chat_id", sa.BigInteger(), primary_key=True),
            sa.Column("user_id", sa.BigInteger(), primary_key=True),
            sa.Column("thread_id", sa.Integer(), primary_key=True),
            sa.Column("business_connection_id", sa.String(128), primary_key=True),
            sa.Column("destiny", sa.String(128), primary_key=True),
            sa.Column("state", sa.String(255), nullable=True),
            sa.Column("data", sa.JSON(), nullable=False),
        )


def downgrade() -> None:
    """Remove the added tables and appointment columns."""
    op.drop_table("fsm_state_records")
    op.drop_table("patient_profiles")
    op.drop_column("appointments", "attendance_status")
    op.drop_column("appointments", "confirmed")
    op.drop_column("appointments", "visit_reason")
