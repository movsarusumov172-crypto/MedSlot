"""Appointment lookup, booking, cancellation and day closure services."""

from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from medslot import texts
from medslot.models import (
    Appointment,
    AppointmentStatus,
    AttendanceStatus,
    DayClosure,
    Doctor,
    PatientProfile,
    WorkSchedule,
)
from medslot.scheduling import ScheduleWindow, Slot, generate_slots, validate_schedule

CANCELLATION_NOTICE = timedelta(hours=2)


@dataclass(frozen=True, slots=True)
class DailyAppointment:
    """Appointment details needed by the clinic's day view."""

    appointment: Appointment
    full_name: str | None
    phone: str | None
    no_show_count: int


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
    exclude_appointment_id: int | None = None,
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
    booked_query = select(Appointment.starts_at).where(
        Appointment.doctor_id == doctor_id,
        Appointment.status == AppointmentStatus.BOOKED.value,
        Appointment.starts_at >= start,
        Appointment.starts_at <= finish,
    )
    if exclude_appointment_id is not None:
        booked_query = booked_query.where(Appointment.id != exclude_appointment_id)
    booked = await session.scalars(booked_query)
    busy_starts = {as_utc(value) for value in booked}
    return [slot for slot in candidates if slot.starts_at not in busy_starts]


async def book_appointment(
    session: AsyncSession,
    patient_id: int,
    doctor_id: int,
    starts_at: datetime,
    timezone_name: str,
    now: datetime | None = None,
    visit_reason: str | None = None,
) -> Appointment:
    """Book one currently available slot and reject concurrent duplicate inserts."""
    if visit_reason is not None and len(visit_reason) > 200:
        raise ValueError(texts.REASON_TOO_LONG)
    if await session.get(PatientProfile, patient_id) is None:
        raise ValueError(texts.PROFILE_REQUIRED)
    starts_at = as_utc(starts_at)
    current_time = as_utc(now or datetime.now(UTC))
    local_date = starts_at.astimezone(ZoneInfo(timezone_name)).date()
    matching_slots = await available_slots(
        session, doctor_id, local_date, timezone_name, current_time
    )
    if not any(slot.starts_at == starts_at for slot in matching_slots):
        raise ValueError(texts.SLOT_UNAVAILABLE)

    appointment = Appointment(
        doctor_id=doctor_id,
        patient_telegram_id=patient_id,
        starts_at=starts_at,
        visit_reason=visit_reason,
        status=AppointmentStatus.BOOKED.value,
        reminder_24h_sent=current_time > starts_at - timedelta(hours=24),
        reminder_2h_sent=current_time > starts_at - timedelta(hours=2),
    )
    session.add(appointment)
    try:
        await session.commit()
    except IntegrityError as error:
        await session.rollback()
        raise ValueError(texts.SLOT_TAKEN) from error
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
        raise ValueError(texts.BOOKING_NOT_FOUND)
    if not can_cancel(appointment.starts_at, now):
        raise ValueError(texts.BOOKING_TOO_LATE)
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
        raise ValueError(texts.ADMIN_SCHEDULE_REQUIRED)
    if not name.strip() or not specialty.strip():
        raise ValueError(texts.ADMIN_DOCTOR_DETAILS_REQUIRED)
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
        raise ValueError(texts.ADMIN_DOCTOR_MISSING)
    closure = DayClosure(
        doctor_id=doctor_id,
        local_date=local_date.isoformat(),
        reason=reason.strip()[:200] or texts.ADMIN_DAY_DEFAULT_REASON,
    )
    session.add(closure)
    try:
        affected = await appointments_for_day(session, doctor_id, local_date, timezone_name)
        for appointment in affected:
            appointment.status = AppointmentStatus.CLOSED.value
        await session.commit()
    except IntegrityError as error:
        await session.rollback()
        raise ValueError(texts.ADMIN_DAY_ALREADY_CLOSED) from error
    return affected


async def open_doctor_day(session: AsyncSession, doctor_id: int, local_date: date) -> bool:
    """Remove a closure; previous closed appointments remain cancelled."""
    closure = await session.scalar(
        select(DayClosure).where(
            DayClosure.doctor_id == doctor_id,
            DayClosure.local_date == local_date.isoformat(),
        )
    )
    if closure is None:
        return False
    await session.delete(closure)
    await session.commit()
    return True


async def update_doctor_details(
    session: AsyncSession,
    doctor_id: int,
    name: str | None = None,
    specialty: str | None = None,
) -> Doctor:
    """Update a doctor's display name or specialty after validating its length."""
    doctor = await session.get(Doctor, doctor_id)
    if doctor is None:
        raise ValueError(texts.DOCTOR_UNAVAILABLE)
    if name is not None:
        normalized_name = name.strip()
        if not normalized_name or len(normalized_name) > 120:
            raise ValueError(texts.ADMIN_DOCTOR_DETAILS_LENGTH)
        doctor.name = normalized_name
    if specialty is not None:
        normalized_specialty = specialty.strip()
        if not normalized_specialty or len(normalized_specialty) > 120:
            raise ValueError(texts.ADMIN_DOCTOR_DETAILS_LENGTH)
        doctor.specialty = normalized_specialty
    await session.commit()
    return doctor


async def replace_doctor_schedule(
    session: AsyncSession, doctor_id: int, schedules: list[ScheduleWindow]
) -> Doctor:
    """Replace all recurring work intervals after validating their boundaries."""
    validate_schedule(schedules)
    if not schedules:
        raise ValueError(texts.ADMIN_SCHEDULE_EMPTY)
    doctor = await session.scalar(
        select(Doctor).options(selectinload(Doctor.schedules)).where(Doctor.id == doctor_id)
    )
    if doctor is None:
        raise ValueError(texts.DOCTOR_UNAVAILABLE)
    doctor.schedules = [
        WorkSchedule(
            weekday=window.weekday,
            start_time=window.start_time,
            end_time=window.end_time,
            duration_minutes=window.duration_minutes,
        )
        for window in schedules
    ]
    await session.commit()
    return doctor


async def set_doctor_active(session: AsyncSession, doctor_id: int, is_active: bool) -> Doctor:
    """Activate or deactivate a doctor without deleting booking history."""
    doctor = await session.get(Doctor, doctor_id)
    if doctor is None:
        raise ValueError(texts.DOCTOR_UNAVAILABLE)
    doctor.is_active = is_active
    await session.commit()
    return doctor


async def confirm_appointment(
    session: AsyncSession, appointment_id: int, patient_id: int
) -> Appointment:
    """Record the patient's confirmation on their active appointment."""
    appointment = await session.scalar(
        select(Appointment).where(
            Appointment.id == appointment_id,
            Appointment.patient_telegram_id == patient_id,
            Appointment.status == AppointmentStatus.BOOKED.value,
        )
    )
    if appointment is None:
        raise ValueError(texts.BOOKING_NOT_FOUND)
    appointment.confirmed = True
    await session.commit()
    return appointment


async def mark_attendance(
    session: AsyncSession,
    appointment_id: int,
    attendance_status: AttendanceStatus,
    now: datetime | None = None,
) -> Appointment:
    """Record arrival or no-show only after the appointment start time."""
    appointment = await session.get(Appointment, appointment_id)
    if appointment is None or appointment.status != AppointmentStatus.BOOKED.value:
        raise ValueError(texts.BOOKING_NOT_FOUND)
    if attendance_status not in {AttendanceStatus.ATTENDED, AttendanceStatus.NO_SHOW}:
        raise ValueError(texts.ADMIN_ATTENDANCE_AFTER_START)
    if as_utc(appointment.starts_at) > as_utc(now or datetime.now(UTC)):
        raise ValueError(texts.ADMIN_ATTENDANCE_AFTER_START)
    appointment.attendance_status = attendance_status.value
    await session.commit()
    return appointment


async def no_show_count(session: AsyncSession, patient_id: int) -> int:
    """Count a patient's appointments marked as no-shows."""
    count = await session.scalar(
        select(func.count(Appointment.id)).where(
            Appointment.patient_telegram_id == patient_id,
            Appointment.attendance_status == AttendanceStatus.NO_SHOW.value,
        )
    )
    return int(count or 0)


async def daily_appointments(
    session: AsyncSession,
    doctor_id: int,
    local_date: date,
    timezone_name: str,
) -> list[DailyAppointment]:
    """Return a doctor's day with patient contacts and each patient's no-show count."""
    no_show_counts = (
        select(
            Appointment.patient_telegram_id.label("patient_id"),
            func.count(Appointment.id).label("no_show_count"),
        )
        .where(Appointment.attendance_status == AttendanceStatus.NO_SHOW.value)
        .group_by(Appointment.patient_telegram_id)
        .subquery()
    )
    query = (
        select(Appointment, PatientProfile, func.coalesce(no_show_counts.c.no_show_count, 0))
        .outerjoin(PatientProfile, PatientProfile.telegram_id == Appointment.patient_telegram_id)
        .outerjoin(no_show_counts, no_show_counts.c.patient_id == Appointment.patient_telegram_id)
        .where(
            Appointment.doctor_id == doctor_id,
            Appointment.status == AppointmentStatus.BOOKED.value,
        )
        .order_by(Appointment.starts_at)
    )
    day_appointments = await appointments_for_day(session, doctor_id, local_date, timezone_name)
    if not day_appointments:
        return []
    appointment_ids = [appointment.id for appointment in day_appointments]
    rows = await session.execute(query.where(Appointment.id.in_(appointment_ids)))
    return [
        DailyAppointment(
            appointment=appointment,
            full_name=profile.full_name if profile else None,
            phone=profile.phone if profile else None,
            no_show_count=int(count),
        )
        for appointment, profile, count in rows
    ]


async def reschedule_appointment(
    session: AsyncSession,
    appointment_id: int,
    patient_id: int,
    new_starts_at: datetime,
    timezone_name: str,
    now: datetime | None = None,
) -> Appointment:
    """Move one appointment transactionally, retaining the old slot on conflict."""
    appointment = await session.scalar(
        select(Appointment).where(
            Appointment.id == appointment_id,
            Appointment.patient_telegram_id == patient_id,
            Appointment.status == AppointmentStatus.BOOKED.value,
        )
    )
    if appointment is None:
        raise ValueError(texts.BOOKING_NOT_FOUND)
    current_time = as_utc(now or datetime.now(UTC))
    if not can_cancel(appointment.starts_at, current_time):
        raise ValueError(texts.BOOKING_TOO_LATE)
    new_starts_at = as_utc(new_starts_at)
    if new_starts_at == as_utc(appointment.starts_at):
        raise ValueError(texts.SAME_SLOT)
    local_date = new_starts_at.astimezone(ZoneInfo(timezone_name)).date()
    candidates = await available_slots(
        session,
        appointment.doctor_id,
        local_date,
        timezone_name,
        current_time,
        exclude_appointment_id=appointment.id,
    )
    if not any(slot.starts_at == new_starts_at for slot in candidates):
        raise ValueError(texts.SLOT_UNAVAILABLE)

    appointment.starts_at = new_starts_at
    appointment.confirmed = False
    appointment.attendance_status = AttendanceStatus.UNMARKED.value
    appointment.reminder_24h_sent = current_time > new_starts_at - timedelta(hours=24)
    appointment.reminder_2h_sent = current_time > new_starts_at - timedelta(hours=2)
    try:
        await session.commit()
    except IntegrityError as error:
        await session.rollback()
        raise ValueError(texts.SLOT_TAKEN) from error
    await session.refresh(appointment)
    return appointment
