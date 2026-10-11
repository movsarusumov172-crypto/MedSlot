"""Patient handlers for browsing slots, booking and managing appointments."""

from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

from aiogram import F, Router
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from medslot.booking import available_slots, book_appointment, doctors_for_specialty, specialties
from medslot.keyboards import (
    BookingCb,
    calendar_keyboard,
    choices_keyboard,
    parse_slot_value,
    slots_keyboard,
)
from medslot.models import Doctor
from medslot.states import BookingStates

router = Router(name="patient")


@router.message(CommandStart())
async def start(message: Message, state: FSMContext) -> None:
    """Reset any unfinished flow and show the patient entry points."""
    await state.clear()
    await message.answer(
        "Здравствуйте! Здесь можно выбрать врача и время приёма.\n"
        "/book — записаться\n/mine — мои записи\n/cancel — выйти из текущего шага"
    )


@router.message(Command("cancel"))
async def cancel_flow(message: Message, state: FSMContext) -> None:
    """Allow the patient to leave any active booking step."""
    await state.clear()
    await message.answer("Действие отменено. /book — начать запись заново.")


@router.message(Command("book"))
async def begin_booking(message: Message, state: FSMContext, session: AsyncSession) -> None:
    """Show specialties and begin the guided booking flow."""
    available_specialties = await specialties(session)
    if not available_specialties:
        await message.answer("Пока нет врачей с доступным графиком.")
        return
    await state.clear()
    await state.set_state(BookingStates.choosing_doctor)
    # Callback data is capped at 64 bytes, too little for Cyrillic names: buttons carry the
    # specialty's index in the list remembered in the FSM state.
    await state.update_data(specialties=available_specialties)
    buttons = [(specialty, str(index)) for index, specialty in enumerate(available_specialties)]
    await message.answer(
        "Выберите специальность:", reply_markup=choices_keyboard(buttons, "specialty")
    )


@router.callback_query(BookingCb.filter(F.action == "specialty"))
async def choose_specialty(
    callback: CallbackQuery,
    callback_data: BookingCb,
    state: FSMContext,
    session: AsyncSession,
) -> None:
    """Show doctors for the selected specialty."""
    known_specialties = (await state.get_data()).get("specialties", [])
    try:
        specialty = known_specialties[int(callback_data.value)]
    except (ValueError, IndexError):
        await callback.answer("Начните запись заново: /book", show_alert=True)
        return
    doctors = await doctors_for_specialty(session, specialty)
    await callback.answer()
    if not doctors:
        await callback.message.edit_text("Для этой специальности сейчас нет врачей.")
        return
    await state.update_data(specialty=specialty)
    buttons = [(doctor.name, str(doctor.id)) for doctor in doctors]
    await callback.message.edit_text(
        "Выберите врача:", reply_markup=choices_keyboard(buttons, "doctor")
    )


@router.callback_query(BookingCb.filter(F.action == "doctor"))
async def choose_doctor(
    callback: CallbackQuery,
    callback_data: BookingCb,
    state: FSMContext,
    session: AsyncSession,
    timezone_name: str,
) -> None:
    """Validate a doctor selection and show the local calendar."""
    try:
        doctor_id = int(callback_data.value)
    except ValueError:
        await callback.answer("Неверный врач", show_alert=True)
        return
    doctor = await session.get(Doctor, doctor_id)
    if doctor is None or not doctor.is_active:
        await callback.answer("Врач недоступен", show_alert=True)
        return
    await state.update_data(doctor_id=doctor_id)
    await state.set_state(BookingStates.choosing_date)
    today = datetime.now(ZoneInfo(timezone_name)).date()
    await callback.answer()
    await callback.message.edit_text(
        f"Выберите дату приёма у врача {doctor.name}:",
        reply_markup=calendar_keyboard(today.year, today.month, today=today),
    )


@router.callback_query(BookingCb.filter(F.action == "month"))
async def change_month(
    callback: CallbackQuery,
    callback_data: BookingCb,
    state: FSMContext,
    timezone_name: str,
) -> None:
    """Move the booking calendar one month backward or forward."""
    year_text, month_text, direction = callback_data.value.split("-")
    year, month = int(year_text), int(month_text)
    if direction == "prev":
        year, month = (year - 1, 12) if month == 1 else (year, month - 1)
    else:
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)
    current_month = datetime.now(ZoneInfo(timezone_name)).date().replace(day=1)
    if date(year, month, 1) < current_month:
        year, month = current_month.year, current_month.month
    await state.set_state(BookingStates.choosing_date)
    await callback.answer()
    await callback.message.edit_reply_markup(
        reply_markup=calendar_keyboard(
            year, month, today=datetime.now(ZoneInfo(timezone_name)).date()
        )
    )


@router.callback_query(BookingCb.filter(F.action == "noop"))
async def answer_calendar_cell(callback: CallbackQuery) -> None:
    """Acknowledge calendar headings and blank cells."""
    await callback.answer()


@router.callback_query(BookingCb.filter(F.action == "day"))
async def choose_day(
    callback: CallbackQuery,
    callback_data: BookingCb,
    state: FSMContext,
    session: AsyncSession,
    timezone_name: str,
) -> None:
    """Show generated and still-free slots for the selected local date."""
    data = await state.get_data()
    doctor_id = data.get("doctor_id")
    if not isinstance(doctor_id, int):
        await callback.answer("Начните запись заново: /book", show_alert=True)
        return
    try:
        selected_date = date.fromisoformat(callback_data.value)
    except ValueError:
        await callback.answer("Неверная дата", show_alert=True)
        return
    today = datetime.now(ZoneInfo(timezone_name)).date()
    if selected_date < today:
        await callback.answer("Выберите сегодняшнюю или будущую дату", show_alert=True)
        return
    slots = await available_slots(session, doctor_id, selected_date, timezone_name)
    await state.update_data(local_date=selected_date.isoformat())
    await state.set_state(BookingStates.choosing_slot)
    await callback.answer()
    if not slots:
        await callback.message.edit_text("На эту дату свободных слотов нет.")
        return
    await callback.message.edit_text(
        "Свободное время:",
        reply_markup=slots_keyboard(doctor_id, slots, ZoneInfo(timezone_name)),
    )


@router.callback_query(BookingCb.filter(F.action == "slot"))
async def choose_slot(
    callback: CallbackQuery,
    callback_data: BookingCb,
    state: FSMContext,
    session: AsyncSession,
    timezone_name: str,
) -> None:
    """Revalidate and atomically reserve the selected slot."""
    try:
        doctor_id, starts_at = parse_slot_value(callback_data.value)
        patient_id = callback.from_user.id
        appointment = await book_appointment(
            session, patient_id, doctor_id, starts_at, timezone_name
        )
    except (ValueError, OverflowError) as error:
        await callback.answer(str(error), show_alert=True)
        return
    doctor = await session.get(Doctor, appointment.doctor_id)
    local_start = appointment.starts_at.replace(tzinfo=UTC).astimezone(ZoneInfo(timezone_name))
    await state.clear()
    await callback.answer("Записано")
    await callback.message.edit_text(
        f"Вы записаны к врачу {doctor.name} на {local_start:%d.%m.%Y в %H:%M}. "
        f"Номер записи: {appointment.id}."
    )
