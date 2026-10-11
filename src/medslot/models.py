"""SQLAlchemy models for doctors, schedules, closures and appointments."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    func,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    """Base class for the application's database models."""


class AppointmentStatus(StrEnum):
    """Lifecycle states retained for appointment history."""

    BOOKED = "booked"
    CANCELLED = "cancelled"
    CLOSED = "closed"


class Doctor(Base):
    """A clinician and the specialty used to find them."""

    __tablename__ = "doctors"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    specialty: Mapped[str] = mapped_column(String(120), index=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    schedules: Mapped[list[WorkSchedule]] = relationship(
        back_populates="doctor", cascade="all, delete-orphan"
    )


class WorkSchedule(Base):
    """A recurring weekly work interval for a doctor."""

    __tablename__ = "work_schedules"
    __table_args__ = (
        CheckConstraint("weekday >= 0 AND weekday <= 6", name="ck_schedule_weekday"),
        CheckConstraint("duration_minutes > 0", name="ck_schedule_duration"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    doctor_id: Mapped[int] = mapped_column(ForeignKey("doctors.id", ondelete="CASCADE"))
    weekday: Mapped[int] = mapped_column(index=True)
    start_time: Mapped[str] = mapped_column(String(5))
    end_time: Mapped[str] = mapped_column(String(5))
    duration_minutes: Mapped[int] = mapped_column()
    doctor: Mapped[Doctor] = relationship(back_populates="schedules")


class DayClosure(Base):
    """A doctor's local calendar day blocked by leave or illness."""

    __tablename__ = "day_closures"
    __table_args__ = (Index("uq_doctor_closed_day", "doctor_id", "local_date", unique=True),)

    id: Mapped[int] = mapped_column(primary_key=True)
    doctor_id: Mapped[int] = mapped_column(ForeignKey("doctors.id", ondelete="CASCADE"))
    local_date: Mapped[str] = mapped_column(String(10))
    reason: Mapped[str] = mapped_column(String(200))


class Appointment(Base):
    """A booked slot; cancelled records remain to preserve history."""

    __tablename__ = "appointments"
    __table_args__ = (
        Index(
            "uq_active_doctor_slot",
            "doctor_id",
            "starts_at",
            unique=True,
            sqlite_where=text("status = 'booked'"),
        ),
        Index("ix_appointment_patient_status", "patient_telegram_id", "status"),
        Index("ix_appointment_start_status", "starts_at", "status"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    doctor_id: Mapped[int] = mapped_column(ForeignKey("doctors.id", ondelete="RESTRICT"))
    patient_telegram_id: Mapped[int] = mapped_column(BigInteger, index=True)
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    status: Mapped[str] = mapped_column(String(16), default=AppointmentStatus.BOOKED.value)
    reminder_24h_sent: Mapped[bool] = mapped_column(Boolean, default=False)
    reminder_2h_sent: Mapped[bool] = mapped_column(Boolean, default=False)
    doctor: Mapped[Doctor] = relationship()
