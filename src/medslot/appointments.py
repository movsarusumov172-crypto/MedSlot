"""Patient commands and callbacks for active appointment history."""

from datetime import UTC
from zoneinfo import ZoneInfo

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from medslot.booking import cancel_appointment
from medslot.keyboards import BookingCb, appointments_keyboard
from medslot.models import Appointment, AppointmentStatus

router = Router(name="appointments")


@router.message(Command("mine"))
async def my_appointments(message: Message, session: AsyncSession, timezone_name: str) -> None:
    """List the patient's active appointments and cancellation actions."""
    result = await session.scalars(
        select(Appointment)
        .options(joinedload(Appointment.doctor))
        .where(
            Appointment.patient_telegram_id == message.from_user.id,
            Appointment.status == AppointmentStatus.BOOKED.value,
        )
        .order_by(Appointment.starts_at)
    )
    appointments = list(result)
    if not appointments:
        await message.answer("У вас нет активных записей.")
        return
    zone = ZoneInfo(timezone_name)
    rows = [
        f"#{item.id} — {item.doctor.name}, "
        f"{item.starts_at.replace(tzinfo=UTC).astimezone(zone):%d.%m.%Y %H:%M}"
        for item in appointments
    ]
    await message.answer(
        "Ваши записи:\n" + "\n".join(rows),
        reply_markup=appointments_keyboard([item.id for item in appointments]),
    )


@router.callback_query(BookingCb.filter(F.action == "cancel"))
async def cancel_booking(
    callback: CallbackQuery, callback_data: BookingCb, session: AsyncSession
) -> None:
    """Cancel only the owner's appointment and enforce the two-hour rule."""
    try:
        appointment_id = int(callback_data.value)
        await cancel_appointment(session, appointment_id, callback.from_user.id)
    except ValueError as error:
        await callback.answer(str(error), show_alert=True)
        return
    await callback.answer("Запись отменена")
    if callback.message:
        await callback.message.edit_text(f"Запись #{appointment_id} отменена.")
