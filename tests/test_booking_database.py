"""SQLite checks for active-slot uniqueness under duplicate booking attempts."""

from datetime import UTC, datetime

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from medslot.models import Appointment, Base, Doctor


@pytest.mark.asyncio
async def test_database_rejects_duplicate_active_slot() -> None:
    """The partial unique index prevents two active bookings for one doctor slot."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    start = datetime(2030, 1, 7, 9, 0, tzinfo=UTC)

    async with session_factory() as session:
        doctor = Doctor(name="Dr Example", specialty="General practice")
        session.add(doctor)
        await session.flush()
        session.add(Appointment(doctor_id=doctor.id, patient_telegram_id=1, starts_at=start))
        await session.commit()

        session.add(Appointment(doctor_id=doctor.id, patient_telegram_id=2, starts_at=start))
        with pytest.raises(IntegrityError):
            await session.commit()
        await session.rollback()

    await engine.dispose()
