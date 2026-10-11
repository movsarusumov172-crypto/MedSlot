"""End-to-end booking rules on a real SQLite database."""

from collections.abc import AsyncIterator
from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from medslot.booking import (
    add_doctor,
    available_slots,
    book_appointment,
    cancel_appointment,
    close_doctor_day,
)
from medslot.models import AppointmentStatus, Base
from medslot.scheduling import ScheduleWindow

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
