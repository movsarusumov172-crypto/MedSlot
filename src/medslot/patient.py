"""Patient profile, booking, and appointment selection handlers."""

from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

from aiogram import F, Router
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message, ReplyKeyboardRemove
from sqlalchemy.ext.asyncio import AsyncSession

from medslot import texts
from medslot.booking import (
    available_slots,
    book_appointment,
    doctors_for_specialty,
    reschedule_appointment,
    specialties,
)
from medslot.keyboards import (
    BookingCb,
    ProfileCb,
    calendar_keyboard,
    choices_keyboard,
    consent_keyboard,
    contact_keyboard,
    parse_slot_value,
    profile_keyboard,
    slots_keyboard,
)
from medslot.models import Doctor
from medslot.patients import (
    get_profile,
    normalize_phone,
    save_profile,
    update_full_name,
    update_phone,
)
from medslot.states import BookingStates, PatientProfileStates

router = Router(name="patient")


async def show_specialties(message: Message, state: FSMContext, session: AsyncSession) -> None:
    """Start a new booking after the patient profile is available."""
    available_specialties = await specialties(session)
    if not available_specialties:
        await message.answer(texts.NO_DOCTORS)
        return
    await state.clear()
    await state.set_state(BookingStates.choosing_doctor)
    await state.update_data(specialties=available_specialties)
    buttons = [(specialty, str(index)) for index, specialty in enumerate(available_specialties)]
    await message.answer(
        texts.CHOOSE_SPECIALTY, reply_markup=choices_keyboard(buttons, "specialty")
    )


async def ask_profile_for_booking(
    message: Message, state: FSMContext, session: AsyncSession
) -> None:
    """Ask first-time patients for consented contact details before booking."""
    if await get_profile(session, message.from_user.id):
        await show_specialties(message, state, session)
        return
    await state.clear()
    await state.update_data(flow_after_profile="book")
    await state.set_state(PatientProfileStates.full_name)
    await message.answer(texts.PROFILE_REQUIRED + "\n\n" + texts.PROFILE_NAME_PROMPT)


@router.message(CommandStart())
async def start(message: Message, state: FSMContext) -> None:
    """Reset an unfinished flow and show patient entry points."""
    await state.clear()
    await message.answer(texts.START)


@router.message(Command("privacy"))
async def privacy(message: Message) -> None:
    """Explain what patient contact data is used for."""
    await message.answer(texts.PRIVACY_NOTICE)


@router.message(Command("cancel"))
async def cancel_flow(message: Message, state: FSMContext) -> None:
    """Allow the patient to leave any active flow."""
    await state.clear()
    await message.answer(texts.CANCELLED, reply_markup=ReplyKeyboardRemove())


@router.message(Command("book"))
async def begin_booking(message: Message, state: FSMContext, session: AsyncSession) -> None:
    """Check the patient profile before showing booking choices."""
    await ask_profile_for_booking(message, state, session)


@router.message(Command("profile"))
async def show_profile(message: Message, session: AsyncSession, state: FSMContext) -> None:
    """Display the patient's saved details and available edit actions."""
    profile = await get_profile(session, message.from_user.id)
    if profile is None:
        await state.clear()
        await state.update_data(flow_after_profile="profile")
        await state.set_state(PatientProfileStates.full_name)
        await message.answer(texts.PROFILE_MISSING + "\n" + texts.PROFILE_NAME_PROMPT)
        return
    await message.answer(
        texts.PROFILE_VIEW.format(
            full_name=profile.full_name,
            phone=profile.phone,
            consented_at=profile.consented_at.strftime(texts.DATE_FORMAT),
        ),
        reply_markup=profile_keyboard(),
    )


@router.callback_query(ProfileCb.filter(F.action.in_({"name", "phone"})))
async def edit_profile(
    callback: CallbackQuery, callback_data: ProfileCb, state: FSMContext
) -> None:
    """Begin an edit of one profile field."""
    if callback_data.action == "name":
        await state.set_state(PatientProfileStates.full_name)
        await state.update_data(profile_edit="name")
        prompt = texts.PROFILE_EDIT_PROMPT
    else:
        await state.set_state(PatientProfileStates.phone)
        await state.update_data(profile_edit="phone")
        prompt = texts.PROFILE_PHONE_PROMPT
    await callback.answer()
    if callback.message:
        await callback.message.answer(
            prompt, reply_markup=contact_keyboard() if callback_data.action == "phone" else None
        )


@router.message(PatientProfileStates.full_name)
async def receive_profile_name(message: Message, state: FSMContext, session: AsyncSession) -> None:
    """Validate the name and continue to phone collection or consent."""
    try:
        from medslot.patients import validate_full_name

        full_name = validate_full_name(message.text or "")
    except ValueError as error:
        await message.answer(str(error))
        return
    data = await state.get_data()
    await state.update_data(full_name=full_name)
    if data.get("profile_edit") == "name":
        await update_full_name(session, message.from_user.id, full_name)
        await state.clear()
        await message.answer(texts.PROFILE_UPDATED)
        return
    await state.set_state(PatientProfileStates.phone)
    await message.answer(texts.PROFILE_PHONE_PROMPT, reply_markup=contact_keyboard())


@router.message(PatientProfileStates.phone)
async def receive_profile_phone(message: Message, state: FSMContext, session: AsyncSession) -> None:
    """Validate a shared contact or manually entered phone number."""
    data = await state.get_data()
    if message.contact is not None:
        if message.contact.user_id != message.from_user.id:
            await message.answer(texts.CONTACT_NOT_OWNED)
            return
        phone_input = message.contact.phone_number
    else:
        phone_input = message.text or ""
    try:
        phone = normalize_phone(phone_input)
    except ValueError as error:
        await message.answer(str(error), reply_markup=contact_keyboard())
        return
    if data.get("profile_edit") == "phone":
        await update_phone(session, message.from_user.id, phone)
        await state.clear()
        await message.answer(texts.PROFILE_UPDATED, reply_markup=ReplyKeyboardRemove())
        return
    await state.update_data(phone=phone)
    await state.set_state(PatientProfileStates.consent)
    await message.answer(texts.CONSENT_PROMPT, reply_markup=ReplyKeyboardRemove())
    await message.answer(texts.CONSENT_ACCEPT, reply_markup=consent_keyboard())


@router.callback_query(ProfileCb.filter(F.action.in_({"agree", "decline"})))
async def consent_choice(
    callback: CallbackQuery,
    callback_data: ProfileCb,
    state: FSMContext,
    session: AsyncSession,
) -> None:
    """Save details only after explicit consent and resume the requested booking."""
    if await state.get_state() != PatientProfileStates.consent.state:
        await callback.answer(texts.CONSENT_REQUIRED, show_alert=True)
        return
    data = await state.get_data()
    if callback_data.action == "decline":
        await state.clear()
        await callback.answer(texts.CONSENT_DECLINED, show_alert=True)
        if callback.message:
            await callback.message.edit_text(texts.CONSENT_DECLINED)
        return
    await save_profile(session, callback.from_user.id, data["full_name"], data["phone"])
    flow_after_profile = data.get("flow_after_profile", "book")
    await callback.answer()
    if callback.message:
        await callback.message.edit_text(texts.PROFILE_SAVED)
    if flow_after_profile == "book" and callback.message:
        await show_specialties(callback.message, state, session)
    else:
        await state.clear()


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
        await callback.answer(texts.RESTART_BOOKING, show_alert=True)
        return
    doctors = await doctors_for_specialty(session, specialty)
    await callback.answer()
    if not doctors:
        await callback.message.edit_text(texts.NO_DOCTORS)
        return
    await state.update_data(specialty=specialty)
    await callback.message.edit_text(
        texts.CHOOSE_DOCTOR,
        reply_markup=choices_keyboard(
            [(doctor.name, str(doctor.id)) for doctor in doctors], "doctor"
        ),
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
        await callback.answer(texts.INVALID_DOCTOR, show_alert=True)
        return
    doctor = await session.get(Doctor, doctor_id)
    if doctor is None or not doctor.is_active:
        await callback.answer(texts.DOCTOR_UNAVAILABLE, show_alert=True)
        return
    await state.update_data(doctor_id=doctor_id)
    await state.set_state(BookingStates.choosing_date)
    today = datetime.now(ZoneInfo(timezone_name)).date()
    await callback.answer()
    await callback.message.edit_text(
        texts.SELECT_DATE.format(name=doctor.name),
        reply_markup=calendar_keyboard(today.year, today.month, today=today),
    )


@router.callback_query(BookingCb.filter(F.action == "month"))
async def change_month(
    callback: CallbackQuery,
    callback_data: BookingCb,
    state: FSMContext,
    timezone_name: str,
) -> None:
    """Move the date calendar one month at a time."""
    year_text, month_text, direction = callback_data.value.split("-")
    year, month = int(year_text), int(month_text)
    if direction == "prev":
        year, month = (year - 1, 12) if month == 1 else (year, month - 1)
    else:
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)
    today = datetime.now(ZoneInfo(timezone_name)).date()
    current_month = today.replace(day=1)
    if date(year, month, 1) < current_month:
        year, month = current_month.year, current_month.month
    await state.set_state(BookingStates.choosing_date)
    await callback.answer()
    await callback.message.edit_reply_markup(
        reply_markup=calendar_keyboard(year, month, today=today)
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
    """Show generated and free slots for the selected local date."""
    data = await state.get_data()
    doctor_id = data.get("doctor_id")
    if not isinstance(doctor_id, int):
        await callback.answer(texts.RESTART_BOOKING, show_alert=True)
        return
    try:
        selected_date = date.fromisoformat(callback_data.value)
    except ValueError:
        await callback.answer(texts.INVALID_DATE, show_alert=True)
        return
    if selected_date < datetime.now(ZoneInfo(timezone_name)).date():
        await callback.answer(texts.FUTURE_DATE_ONLY, show_alert=True)
        return
    slots = await available_slots(session, doctor_id, selected_date, timezone_name)
    await state.update_data(local_date=selected_date.isoformat())
    await state.set_state(BookingStates.choosing_slot)
    await callback.answer()
    if not slots:
        await callback.message.edit_text(texts.NO_FREE_SLOTS)
        return
    await callback.message.edit_text(
        texts.FREE_SLOTS, reply_markup=slots_keyboard(doctor_id, slots, ZoneInfo(timezone_name))
    )


@router.callback_query(BookingCb.filter(F.action == "slot"))
async def choose_slot(
    callback: CallbackQuery,
    callback_data: BookingCb,
    state: FSMContext,
    session: AsyncSession,
    timezone_name: str,
) -> None:
    """Reserve a new slot or ask for a short visit reason before booking."""
    try:
        doctor_id, starts_at = parse_slot_value(callback_data.value)
    except ValueError as error:
        await callback.answer(str(error), show_alert=True)
        return
    data = await state.get_data()
    move_id = data.get("reschedule_id")
    if isinstance(move_id, int):
        try:
            appointment = await reschedule_appointment(
                session, move_id, callback.from_user.id, starts_at, timezone_name
            )
        except ValueError as error:
            await callback.answer(str(error), show_alert=True)
            return
        await state.clear()
        local_start = appointment.starts_at.replace(tzinfo=UTC).astimezone(ZoneInfo(timezone_name))
        await callback.answer()
        await callback.message.edit_text(
            texts.BOOKING_MOVED.format(
                when=local_start.strftime(texts.LOCAL_DATETIME_FORMAT),
                appointment_id=appointment.id,
            )
        )
        return
    await state.update_data(selected_slot=callback_data.value)
    await state.set_state(BookingStates.visit_reason)
    await callback.answer()
    await callback.message.edit_text(
        texts.ASK_VISIT_REASON,
        reply_markup=choices_keyboard([(texts.SKIP_REASON, "skip")], "reason"),
    )


@router.callback_query(BookingCb.filter(F.action == "reason"))
async def skip_visit_reason(
    callback: CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
    timezone_name: str,
) -> None:
    """Complete a booking without a visit reason."""
    await finish_booking(callback, state, session, timezone_name, None)


@router.message(BookingStates.visit_reason)
async def receive_visit_reason(
    message: Message, state: FSMContext, session: AsyncSession, timezone_name: str
) -> None:
    """Validate and save an optional short visit reason."""
    reason = (message.text or "").strip()
    if len(reason) > 200:
        await message.answer(texts.REASON_TOO_LONG)
        return
    await finish_booking(message, state, session, timezone_name, reason or None)


async def finish_booking(
    event: Message | CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
    timezone_name: str,
    reason: str | None,
) -> None:
    """Revalidate the selected slot, create the appointment and report its details."""
    data = await state.get_data()
    try:
        doctor_id, starts_at = parse_slot_value(str(data.get("selected_slot", "")))
        appointment = await book_appointment(
            session, event.from_user.id, doctor_id, starts_at, timezone_name, visit_reason=reason
        )
    except (ValueError, OverflowError) as error:
        if isinstance(event, CallbackQuery):
            await event.answer(str(error), show_alert=True)
        else:
            await event.answer(str(error))
        return
    doctor = await session.get(Doctor, appointment.doctor_id)
    local_start = appointment.starts_at.replace(tzinfo=UTC).astimezone(ZoneInfo(timezone_name))
    await state.clear()
    response = texts.BOOKED.format(
        doctor=doctor.name,
        when=local_start.strftime(texts.LOCAL_DATETIME_FORMAT),
        appointment_id=appointment.id,
    )
    if isinstance(event, CallbackQuery):
        await event.answer()
        await event.message.edit_text(response)
    else:
        await event.answer(response)
