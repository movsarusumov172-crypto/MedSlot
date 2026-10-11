"""Appointment lookup, booking, cancellation and day closure services."""

from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from medslot.models import (
    Appointment,
    AppointmentStatus,
    DayClosure,
    Doctor,
    WorkSchedule,
)
from medslot.scheduling import ScheduleWindow, Slot, generate_slots, validate_schedule

CANCELLATION_NOTICE = timedelta(hours=2)


def as_utc(value: datetime) -> datetime:
    """Normalize SQLite's naive UTC values and aware datetimes to UTC."""
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


async def specialties(session: AsyncSession) -> list[str]:
    """Return specialties with at least one active doctor."""
    result = await session.scalars(
        select(Doctor.specialty)
        .where(Doctor.is_active.is_(True))
        .distinct()
        .order_by(Doctor.specialty)
    )
    return list(result)


async def doctors_for_specialty(session: AsyncSession, specialty: str) -> list[Doctor]:
    """List active doctors for a validated specialty selection."""
    result = await session.scalars(
        select(Doctor)
        .where(Doctor.is_active.is_(True), Doctor.specialty == specialty)
        .order_by(Doctor.name)
    )
    return list(result)


async def doctor_schedule(session: AsyncSession, doctor_id: int) -> list[ScheduleWindow]:
    """Read a doctor's weekly work intervals."""
    rows = await session.scalars(
        select(WorkSchedule)
        .where(WorkSchedule.doctor_id == doctor_id)
        .order_by(WorkSchedule.weekday)
    )
    return [
        ScheduleWindow(row.weekday, row.start_time, row.end_time, row.duration_minutes)
        for row in rows
    ]


async def available_slots(
    session: AsyncSession,
    doctor_id: int,
    local_date: date,
    timezone_name: str,
    now: datetime | None = None,
) -> list[Slot]:
    """Generate schedule slots, then remove closed days and active bookings."""
    doctor = await session.get(Doctor, doctor_id)
    if doctor is None or not doctor.is_active:
        return []
    closure = await session.scalar(
        select(DayClosure.id).where(
            DayClosure.doctor_id == doctor_id,
            DayClosure.local_date == local_date.isoformat(),
        )
    )
    if closure is not None:
        return []

    candidates = generate_slots(
        local_date, await doctor_schedule(session, doctor_id), timezone_name, now
    )
    if not candidates:
        return []
    start = candidates[0].starts_at.replace(tzinfo=None)
    finish = candidates[-1].starts_at.replace(tzinfo=None)
    booked = await session.scalars(
        select(Appointment.starts_at).where(
            Appointment.doctor_id == doctor_id,
            Appointment.status == AppointmentStatus.BOOKED.value,
            Appointment.starts_at >= start,
            Appointment.starts_at <= finish,
        )
    )
    busy_starts = {as_utc(value) for value in booked}
    return [slot for slot in candidates if slot.starts_at not in busy_starts]


async def book_appointment(
    session: AsyncSession,
    patient_id: int,
    doctor_id: int,
    starts_at: datetime,
    timezone_name: str,
    now: datetime | None = None,
) -> Appointment:
    """Book one currently available slot and reject concurrent duplicate inserts."""
    starts_at = as_utc(starts_at)
    current_time = as_utc(now or datetime.now(UTC))
    local_date = starts_at.astimezone(ZoneInfo(timezone_name)).date()
    matching_slots = await available_slots(
        session, doctor_id, local_date, timezone_name, current_time
    )
    if not any(slot.starts_at == starts_at for slot in matching_slots):
        raise ValueError("Это время уже недоступно")

    appointment = Appointment(
        doctor_id=doctor_id,
        patient_telegram_id=patient_id,
        starts_at=starts_at,
        status=AppointmentStatus.BOOKED.value,
        reminder_24h_sent=current_time > starts_at - timedelta(hours=24),
        reminder_2h_sent=current_time > starts_at - timedelta(hours=2),
    )
    session.add(appointment)
    try:
        await session.commit()
    except IntegrityError as error:
        await session.rollback()
        raise ValueError("Это время только что занял другой пациент") from error
    await session.refresh(appointment)
    return appointment


def can_cancel(starts_at: datetime, now: datetime | None = None) -> bool:
    """A booking can be cancelled until two hours before its start."""
    current_time = as_utc(now or datetime.now(UTC))
    return as_utc(starts_at) - current_time >= CANCELLATION_NOTICE


async def cancel_appointment(
    session: AsyncSession,
    appointment_id: int,
    patient_id: int,
    now: datetime | None = None,
) -> Appointment:
    """Cancel only the requesting patient's active appointment within the limit."""
    appointment = await session.scalar(
        select(Appointment).where(
            Appointment.id == appointment_id,
            Appointment.patient_telegram_id == patient_id,
            Appointment.status == AppointmentStatus.BOOKED.value,
        )
    )
    if appointment is None:
        raise ValueError("Запись не найдена")
    if not can_cancel(appointment.starts_at, now):
        raise ValueError("Отменить запись можно не позже чем за 2 часа до приёма")
    appointment.status = AppointmentStatus.CANCELLED.value
    await session.commit()
    return appointment


async def add_doctor(
    session: AsyncSession,
    name: str,
    specialty: str,
    schedules: list[ScheduleWindow],
) -> Doctor:
    """Create a doctor only after validating every recurring work interval."""
    validate_schedule(schedules)
    if not schedules:
        raise ValueError("нужен хотя бы один интервал работы")
    if not name.strip() or not specialty.strip():
        raise ValueError("нужны имя и специальность врача")
    doctor = Doctor(name=name.strip(), specialty=specialty.strip())
    doctor.schedules = [
        WorkSchedule(
            weekday=window.weekday,
            start_time=window.start_time,
            end_time=window.end_time,
            duration_minutes=window.duration_minutes,
        )
        for window in schedules
    ]
    session.add(doctor)
    await session.commit()
    await session.refresh(doctor)
    return doctor


async def appointments_for_day(
    session: AsyncSession,
    doctor_id: int,
    local_date: date,
    timezone_name: str,
) -> list[Appointment]:
    """List active appointments for a doctor's local calendar day."""
    zone = ZoneInfo(timezone_name)
    local_start = datetime.combine(local_date, time.min, tzinfo=zone).astimezone(UTC)
    local_end = datetime.combine(local_date + timedelta(days=1), time.min, tzinfo=zone).astimezone(
        UTC
    )
    result = await session.scalars(
        select(Appointment)
        .where(
            Appointment.doctor_id == doctor_id,
            Appointment.status == AppointmentStatus.BOOKED.value,
            Appointment.starts_at >= local_start.replace(tzinfo=None),
            Appointment.starts_at < local_end.replace(tzinfo=None),
        )
        .order_by(Appointment.starts_at)
    )
    return list(result)


async def close_doctor_day(
    session: AsyncSession,
    doctor_id: int,
    local_date: date,
    reason: str,
    timezone_name: str,
) -> list[Appointment]:
    """Close one doctor's day and mark its booked appointments for notification."""
    if await session.get(Doctor, doctor_id) is None:
        raise ValueError("врач не найден")
    closure = DayClosure(
        doctor_id=doctor_id,
        local_date=local_date.isoformat(),
        reason=reason.strip()[:200] or "День закрыт",
    )
    session.add(closure)
    try:
        affected = await appointments_for_day(session, doctor_id, local_date, timezone_name)
        for appointment in affected:
            appointment.status = AppointmentStatus.CLOSED.value
        await session.commit()
    except IntegrityError as error:
        await session.rollback()
        raise ValueError("этот день у врача уже закрыт") from error
    return affected
