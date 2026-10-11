"""End-to-end booking rules on a real SQLite database."""

from collections.abc import AsyncIterator
from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from medslot import booking as booking_services
from medslot.booking import (
    add_doctor,
    as_utc,
    available_slots,
    book_appointment,
    cancel_appointment,
    close_doctor_day,
    confirm_appointment,
    daily_appointments,
    mark_attendance,
    no_show_count,
    open_doctor_day,
    reschedule_appointment,
)
from medslot.models import AppointmentStatus, AttendanceStatus, Base, PatientProfile
from medslot.patients import normalize_phone, validate_full_name
from medslot.scheduling import ScheduleWindow, Slot

ZONE = "UTC"
MONDAY = date(2030, 1, 7)
NOW = datetime(2030, 1, 1, tzinfo=UTC)
NINE = datetime(2030, 1, 7, 9, 0, tzinfo=UTC)


@pytest.fixture
async def session() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    async with async_sessionmaker(engine, expire_on_commit=False)() as db_session:
        yield db_session
    await engine.dispose()


async def _doctor_id(session: AsyncSession) -> int:
    for patient_id in (1, 2, 3):
        session.add(
            PatientProfile(
                telegram_id=patient_id,
                full_name=f"Пациент {patient_id}",
                phone="+79991234567",
                consented_at=NOW,
            )
        )
    await session.commit()
    doctor = await add_doctor(
        session, "Иванова А. П.", "Терапевт", [ScheduleWindow(0, "09:00", "10:00", 30)]
    )
    return doctor.id


async def test_booked_slot_disappears_and_cannot_be_booked_twice(session: AsyncSession) -> None:
    doctor_id = await _doctor_id(session)

    await book_appointment(session, 1, doctor_id, NINE, ZONE, now=NOW)

    slots = await available_slots(session, doctor_id, MONDAY, ZONE, now=NOW)
    assert [slot.starts_at for slot in slots] == [NINE + timedelta(minutes=30)]
    with pytest.raises(ValueError, match="недоступно"):
        await book_appointment(session, 2, doctor_id, NINE, ZONE, now=NOW)


async def test_cancelled_slot_becomes_free_again(session: AsyncSession) -> None:
    doctor_id = await _doctor_id(session)
    appointment = await book_appointment(session, 1, doctor_id, NINE, ZONE, now=NOW)

    await cancel_appointment(session, appointment.id, patient_id=1, now=NOW)

    slots = await available_slots(session, doctor_id, MONDAY, ZONE, now=NOW)
    assert NINE in [slot.starts_at for slot in slots]


async def test_patient_cannot_cancel_someone_elses_appointment(session: AsyncSession) -> None:
    doctor_id = await _doctor_id(session)
    appointment = await book_appointment(session, 1, doctor_id, NINE, ZONE, now=NOW)

    with pytest.raises(ValueError, match="не найдена"):
        await cancel_appointment(session, appointment.id, patient_id=2, now=NOW)


async def test_closing_a_day_cancels_its_bookings_and_hides_its_slots(
    session: AsyncSession,
) -> None:
    doctor_id = await _doctor_id(session)
    appointment = await book_appointment(session, 1, doctor_id, NINE, ZONE, now=NOW)

    affected = await close_doctor_day(session, doctor_id, MONDAY, "Больничный", ZONE)

    assert [item.id for item in affected] == [appointment.id]
    assert affected[0].status == AppointmentStatus.CLOSED.value
    assert await available_slots(session, doctor_id, MONDAY, ZONE, now=NOW) == []


async def test_reopening_day_restores_slots_but_not_closed_appointments(
    session: AsyncSession,
) -> None:
    doctor_id = await _doctor_id(session)
    appointment = await book_appointment(session, 1, doctor_id, NINE, ZONE, now=NOW)
    await close_doctor_day(session, doctor_id, MONDAY, "Отпуск", ZONE)

    assert await open_doctor_day(session, doctor_id, MONDAY)
    assert not await open_doctor_day(session, doctor_id, MONDAY)
    assert appointment.status == AppointmentStatus.CLOSED.value
    assert NINE in [
        slot.starts_at for slot in await available_slots(session, doctor_id, MONDAY, ZONE, NOW)
    ]


async def test_reschedule_moves_existing_booking_and_keeps_visit_reason(
    session: AsyncSession,
) -> None:
    doctor_id = await _doctor_id(session)
    appointment = await book_appointment(
        session, 1, doctor_id, NINE, ZONE, now=NOW, visit_reason="Плановый осмотр"
    )

    moved = await reschedule_appointment(
        session, appointment.id, 1, NINE + timedelta(minutes=30), ZONE, now=NOW
    )

    assert moved.id == appointment.id
    assert as_utc(moved.starts_at) == NINE + timedelta(minutes=30)
    assert moved.visit_reason == "Плановый осмотр"
    assert NINE in [
        slot.starts_at for slot in await available_slots(session, doctor_id, MONDAY, ZONE, NOW)
    ]


async def test_reschedule_rejects_occupied_slot_without_releasing_old_slot(
    session: AsyncSession,
) -> None:
    doctor_id = await _doctor_id(session)
    first = await book_appointment(session, 1, doctor_id, NINE, ZONE, now=NOW)
    await book_appointment(session, 2, doctor_id, NINE + timedelta(minutes=30), ZONE, now=NOW)

    with pytest.raises(ValueError):
        await reschedule_appointment(
            session, first.id, 1, NINE + timedelta(minutes=30), ZONE, now=NOW
        )

    await session.refresh(first)
    assert as_utc(first.starts_at) == NINE
    assert first.status == AppointmentStatus.BOOKED.value


async def test_reschedule_rolls_back_old_slot_if_unique_index_catches_race(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    doctor_id = await _doctor_id(session)
    first = await book_appointment(session, 1, doctor_id, NINE, ZONE, now=NOW)
    occupied_start = NINE + timedelta(minutes=30)
    await book_appointment(session, 2, doctor_id, occupied_start, ZONE, now=NOW)

    async def report_slot_free(*args: object, **kwargs: object) -> list[Slot]:
        return [Slot(occupied_start, occupied_start + timedelta(minutes=30))]

    monkeypatch.setattr(booking_services, "available_slots", report_slot_free)
    with pytest.raises(ValueError, match="занял другой пациент"):
        await reschedule_appointment(session, first.id, 1, occupied_start, ZONE, now=NOW)

    await session.refresh(first)
    assert as_utc(first.starts_at) == NINE
    assert first.status == AppointmentStatus.BOOKED.value


async def test_reschedule_obeys_two_hour_cutoff(session: AsyncSession) -> None:
    doctor_id = await _doctor_id(session)
    starts_at = NINE
    appointment = await book_appointment(session, 1, doctor_id, starts_at, ZONE, now=NOW)
    late_now = starts_at - timedelta(hours=1)

    with pytest.raises(ValueError, match="2 часа"):
        await reschedule_appointment(
            session,
            appointment.id,
            1,
            starts_at + timedelta(minutes=30),
            ZONE,
            now=late_now,
        )


async def test_no_show_requires_elapsed_start_and_is_counted(session: AsyncSession) -> None:
    doctor_id = await _doctor_id(session)
    appointment = await book_appointment(session, 1, doctor_id, NINE, ZONE, now=NOW)

    with pytest.raises(ValueError):
        await mark_attendance(
            session, appointment.id, AttendanceStatus.NO_SHOW, now=NINE - timedelta(minutes=1)
        )
    await mark_attendance(session, appointment.id, AttendanceStatus.NO_SHOW, now=NINE)

    assert await no_show_count(session, 1) == 1


async def test_confirmation_and_profile_details_are_available_in_clinic_day(
    session: AsyncSession,
) -> None:
    doctor_id = await _doctor_id(session)
    appointment = await book_appointment(session, 1, doctor_id, NINE, ZONE, now=NOW)

    await confirm_appointment(session, appointment.id, 1)
    day_rows = await daily_appointments(session, doctor_id, MONDAY, ZONE)

    assert day_rows[0].appointment.confirmed
    assert day_rows[0].full_name == "Пациент 1"
    assert day_rows[0].phone == "+79991234567"


def test_phone_normalization_and_validation() -> None:
    assert normalize_phone("8 (999) 123-45-67") == "+79991234567"
    assert normalize_phone("+1 202 555 0123") == "+12025550123"
    with pytest.raises(ValueError):
        normalize_phone("abc")


def test_full_name_requires_two_name_parts() -> None:
    assert validate_full_name("  Иван   Иванов ") == "Иван Иванов"
    with pytest.raises(ValueError):
        validate_full_name("Иван")


async def test_booking_requires_profile_and_limits_visit_reason(session: AsyncSession) -> None:
    doctor_id = await _doctor_id(session)
    with pytest.raises(ValueError, match="карточку пациента"):
        await book_appointment(session, 88, doctor_id, NINE, ZONE, now=NOW)

    with pytest.raises(ValueError, match="200 символов"):
        await book_appointment(
            session,
            1,
            doctor_id,
            NINE,
            ZONE,
            now=NOW,
            visit_reason="x" * 201,
        )
