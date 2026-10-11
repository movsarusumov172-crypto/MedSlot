"""Create the original MedSlot tables when they do not yet exist."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261011_0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _tables() -> set[str]:
    return set(sa.inspect(op.get_bind()).get_table_names())


def _indexes(table_name: str) -> set[str]:
    return {index["name"] for index in sa.inspect(op.get_bind()).get_indexes(table_name)}


def upgrade() -> None:
    """Skip legacy tables already created by the original create_all startup."""
    tables = _tables()
    if "doctors" not in tables:
        op.create_table(
            "doctors",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("name", sa.String(120), nullable=False),
            sa.Column("specialty", sa.String(120), nullable=False),
            sa.Column("is_active", sa.Boolean(), nullable=False),
        )
    if "work_schedules" not in tables:
        op.create_table(
            "work_schedules",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column(
                "doctor_id",
                sa.Integer(),
                sa.ForeignKey("doctors.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("weekday", sa.Integer(), nullable=False),
            sa.Column("start_time", sa.String(5), nullable=False),
            sa.Column("end_time", sa.String(5), nullable=False),
            sa.Column("duration_minutes", sa.Integer(), nullable=False),
            sa.CheckConstraint("weekday >= 0 AND weekday <= 6", name="ck_schedule_weekday"),
            sa.CheckConstraint("duration_minutes > 0", name="ck_schedule_duration"),
        )
    if "day_closures" not in tables:
        op.create_table(
            "day_closures",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column(
                "doctor_id",
                sa.Integer(),
                sa.ForeignKey("doctors.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("local_date", sa.String(10), nullable=False),
            sa.Column("reason", sa.String(200), nullable=False),
        )
    if "appointments" not in tables:
        op.create_table(
            "appointments",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column(
                "doctor_id",
                sa.Integer(),
                sa.ForeignKey("doctors.id", ondelete="RESTRICT"),
                nullable=False,
            ),
            sa.Column("patient_telegram_id", sa.BigInteger(), nullable=False),
            sa.Column("starts_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
            sa.Column("status", sa.String(16), nullable=False),
            sa.Column("reminder_24h_sent", sa.Boolean(), nullable=False),
            sa.Column("reminder_2h_sent", sa.Boolean(), nullable=False),
        )

    tables = _tables()
    index_specs = (
        ("doctors", "ix_doctors_specialty", ["specialty"], False, None),
        ("work_schedules", "ix_work_schedules_weekday", ["weekday"], False, None),
        ("day_closures", "uq_doctor_closed_day", ["doctor_id", "local_date"], True, None),
        (
            "appointments",
            "ix_appointments_patient_telegram_id",
            ["patient_telegram_id"],
            False,
            None,
        ),
        (
            "appointments",
            "ix_appointment_patient_status",
            ["patient_telegram_id", "status"],
            False,
            None,
        ),
        (
            "appointments",
            "ix_appointment_start_status",
            ["starts_at", "status"],
            False,
            None,
        ),
        (
            "appointments",
            "uq_active_doctor_slot",
            ["doctor_id", "starts_at"],
            True,
            sa.text("status = 'booked'"),
        ),
    )
    for table, name, columns, unique, predicate in index_specs:
        if table in tables and name not in _indexes(table):
            op.create_index(name, table, columns, unique=unique, sqlite_where=predicate)


def downgrade() -> None:
    """Keep baseline tables because this revision also adopts legacy databases."""
