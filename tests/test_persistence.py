"""Persistent FSM state and reminder delivery behavior."""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest
from aiogram.fsm.storage.base import StorageKey
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from medslot.models import Appointment, Base, Doctor, PatientProfile
from medslot.reminders import deliver_due_reminders
from medslot.sqlite_storage import SqliteFSMStorage


@pytest.mark.asyncio
async def test_dialog_state_and_data_survive_storage_recreation() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    key = StorageKey(bot_id=100, chat_id=200, user_id=200, destiny="default")

    first_process = SqliteFSMStorage(session_factory)
    await first_process.set_state(key, "BookingStates:choosing_slot")
    await first_process.set_data(key, {"doctor_id": 5, "selected_date": "2030-01-07"})
    second_process = SqliteFSMStorage(session_factory)

    assert await second_process.get_state(key) == "BookingStates:choosing_slot"
    assert await second_process.get_data(key) == {
        "doctor_id": 5,
        "selected_date": "2030-01-07",
    }
    await engine.dispose()


@pytest.mark.asyncio
async def test_due_reminder_uses_mock_bot_and_includes_action_buttons() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    starts_at = datetime(2030, 1, 7, 9, 0, tzinfo=UTC)
    async with session_factory() as session:
        doctor = Doctor(name="Врач", specialty="Терапевт")
        session.add(doctor)
        await session.flush()
        session.add(
            PatientProfile(
                telegram_id=77,
                full_name="Тест Пациент",
                phone="+79991234567",
                consented_at=datetime(2030, 1, 1, tzinfo=UTC),
            )
        )
        session.add(Appointment(doctor_id=doctor.id, patient_telegram_id=77, starts_at=starts_at))
        await session.commit()

    bot = AsyncMock()
    await deliver_due_reminders(
        session_factory,
        bot,
        "UTC",
        now=starts_at - timedelta(hours=24),
    )

    bot.send_message.assert_awaited_once()
    sent_markup = bot.send_message.await_args.kwargs["reply_markup"]
    labels = [button.text for row in sent_markup.inline_keyboard for button in row]
    assert labels == ["✅ Приду", "Перенести", "Отменить"]
    await engine.dispose()
