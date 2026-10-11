"""Patient commands and callbacks for active appointment history."""

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from medslot import texts
from medslot.booking import can_cancel, cancel_appointment, confirm_appointment
from medslot.keyboards import BookingCb, appointments_keyboard, calendar_keyboard
from medslot.models import Appointment, AppointmentStatus
from medslot.states import BookingStates

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
        await message.answer(texts.NO_APPOINTMENTS)
        return
    zone = ZoneInfo(timezone_name)
    rows = [
        f"#{item.id} — {item.doctor.name}, "
        f"{item.starts_at.replace(tzinfo=UTC).astimezone(zone):%d.%m.%Y %H:%M}"
        for item in appointments
    ]
    await message.answer(
        texts.MY_APPOINTMENTS_TITLE + "\n" + "\n".join(rows),
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
    await callback.answer(texts.BOOKING_CANCELLED.format(appointment_id=appointment_id))
    if callback.message:
        await callback.message.edit_text(
            texts.BOOKING_CANCELLED.format(appointment_id=appointment_id)
        )


@router.callback_query(BookingCb.filter(F.action == "move"))
async def begin_move(
    callback: CallbackQuery,
    callback_data: BookingCb,
    state: FSMContext,
    session: AsyncSession,
    timezone_name: str,
) -> None:
    """Start a same-doctor reschedule if the two-hour cutoff allows it."""
    try:
        appointment_id = int(callback_data.value)
    except ValueError:
        await callback.answer(texts.BOOKING_NOT_FOUND, show_alert=True)
        return
    appointment = await session.scalar(
        select(Appointment)
        .options(joinedload(Appointment.doctor))
        .where(
            Appointment.id == appointment_id,
            Appointment.patient_telegram_id == callback.from_user.id,
            Appointment.status == AppointmentStatus.BOOKED.value,
        )
    )
    if appointment is None:
        await callback.answer(texts.BOOKING_NOT_FOUND, show_alert=True)
        return
    if not can_cancel(appointment.starts_at):
        await callback.answer(texts.BOOKING_TOO_LATE, show_alert=True)
        return
    await state.clear()
    await state.update_data(doctor_id=appointment.doctor_id, reschedule_id=appointment.id)
    await state.set_state(BookingStates.choosing_date)
    today = datetime.now(ZoneInfo(timezone_name)).date()
    await callback.answer()
    if callback.message:
        await callback.message.answer(
            texts.SELECT_DATE.format(name=appointment.doctor.name),
            reply_markup=calendar_keyboard(today.year, today.month, today=today),
        )


@router.callback_query(BookingCb.filter(F.action == "confirm"))
async def confirm_booking(
    callback: CallbackQuery, callback_data: BookingCb, session: AsyncSession
) -> None:
    """Record a patient's confirmation from a reminder."""
    try:
        await confirm_appointment(session, int(callback_data.value), callback.from_user.id)
    except ValueError as error:
        await callback.answer(str(error), show_alert=True)
        return
    await callback.answer(texts.BOOKING_CONFIRMED, show_alert=True)
