"""Button-based clinic administration handlers."""

from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

from aiogram import Bot, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from medslot import texts
from medslot.booking import (
    add_doctor,
    close_doctor_day,
    daily_appointments,
    mark_attendance,
    open_doctor_day,
    replace_doctor_schedule,
    set_doctor_active,
    update_doctor_details,
)
from medslot.config import Settings
from medslot.delivery import send_notice
from medslot.keyboards import (
    AdminCb,
    admin_calendar_keyboard,
    admin_day_keyboard,
    admin_doctor_card_keyboard,
    admin_doctors_keyboard,
    admin_menu_keyboard,
)
from medslot.models import AttendanceStatus, Doctor
from medslot.scheduling import ScheduleWindow, validate_schedule
from medslot.states import AdminDayStates, DoctorSetupStates

router = Router(name="admin")


def is_admin(user_id: int | None, settings: Settings) -> bool:
    """Authorize an action using configured Telegram user IDs."""
    return user_id is not None and user_id in settings.admin_ids


@router.message(Command("admin"))
async def admin_menu(message: Message, settings: Settings) -> None:
    """Show the admin menu to configured clinic staff."""
    if not is_admin(message.from_user.id if message.from_user else None, settings):
        return
    await message.answer(texts.ADMIN_MENU, reply_markup=admin_menu_keyboard())


@router.callback_query(AdminCb.filter())
async def admin_actions(
    callback: CallbackQuery,
    callback_data: AdminCb,
    state: FSMContext,
    session: AsyncSession,
    settings: Settings,
    timezone_name: str,
    bot: Bot,
) -> None:
    """Authorize and dispatch one admin button action."""
    if not is_admin(callback.from_user.id, settings):
        await callback.answer(texts.ERROR_GENERIC, show_alert=True)
        return
    await callback.answer()
    action = callback_data.action
    if action in {"menu", "doctors", "add_doctor", "day_doctor"}:
        await handle_admin_navigation(callback, action, state, session)
        return
    if action in {"doctor", "edit_name", "edit_specialty", "edit_schedule", "toggle"}:
        await handle_doctor_action(
            callback, callback_data.value, action, state, session, timezone_name
        )
        return
    if action in {"calendar_month", "noop", "day", "close_day", "open_day"}:
        await handle_schedule_action(
            callback, callback_data.value, action, state, session, bot, timezone_name
        )
        return
    if action in {"attended", "no_show"}:
        await handle_attendance(callback, callback_data.value, action, session)


async def handle_admin_navigation(
    callback: CallbackQuery, action: str, state: FSMContext, session: AsyncSession
) -> None:
    """Open the menu, doctor picker, or guided add-doctor flow."""
    if action == "menu":
        await callback.message.edit_text(texts.ADMIN_MENU, reply_markup=admin_menu_keyboard())
        return
    if action == "doctors":
        doctors = list(await session.scalars(select(Doctor).order_by(Doctor.name)))
        label = texts.ADMIN_DOCTOR_LIST_EMPTY if not doctors else texts.ADMIN_DOCTORS
        await callback.message.edit_text(label, reply_markup=admin_doctors_keyboard(doctors))
        return
    if action == "add_doctor":
        await state.clear()
        await state.set_state(DoctorSetupStates.doctor_details)
        await callback.message.answer(texts.ADMIN_ADD_DOCTOR_PROMPT)
        return
    doctors = list(await session.scalars(select(Doctor).order_by(Doctor.name)))
    await callback.message.edit_text(
        texts.ADMIN_CHOOSE_DOCTOR, reply_markup=admin_doctors_keyboard(doctors)
    )
    await state.update_data(admin_action="choose_day_doctor")


async def handle_doctor_action(
    callback: CallbackQuery,
    doctor_id_text: str,
    action: str,
    state: FSMContext,
    session: AsyncSession,
    timezone_name: str,
) -> None:
    """Open a doctor card, start an edit, or change activation status."""
    if action == "doctor":
        if (await state.get_data()).get("admin_action") == "choose_day_doctor":
            await select_day_doctor(callback, doctor_id_text, state, timezone_name)
            return
        await show_doctor_card(callback, doctor_id_text, session)
        return
    if action in {"edit_name", "edit_specialty", "edit_schedule"}:
        states = {
            "edit_name": (DoctorSetupStates.edit_name, texts.ADMIN_EDIT_NAME_PROMPT),
            "edit_specialty": (DoctorSetupStates.edit_specialty, texts.ADMIN_EDIT_SPECIALTY_PROMPT),
            "edit_schedule": (DoctorSetupStates.edit_schedule, texts.ADMIN_EDIT_SCHEDULE_PROMPT),
        }
        selected_state, prompt = states[action]
        await state.set_state(selected_state)
        await state.update_data(doctor_id=int(doctor_id_text))
        await callback.message.answer(prompt)
        return
    if action == "toggle":
        doctor = await session.get(Doctor, int(doctor_id_text))
        if doctor is None:
            await callback.message.answer(texts.DOCTOR_UNAVAILABLE)
            return
        await set_doctor_active(session, doctor.id, not doctor.is_active)
        await show_doctor_card(callback, str(doctor.id), session)


async def handle_schedule_action(
    callback: CallbackQuery,
    value: str,
    action: str,
    state: FSMContext,
    session: AsyncSession,
    bot: Bot,
    timezone_name: str,
) -> None:
    """Render calendars and day views or apply closure actions."""
    if action == "calendar_month":
        await update_admin_calendar(callback, value, timezone_name)
        return
    if action == "noop":
        return
    if action == "day":
        await render_day(callback, value, state, session, timezone_name)
        return
    if action in {"close_day", "open_day"}:
        await handle_day_toggle(callback, value, action, state, session, bot, timezone_name)


async def show_doctor_card(
    callback: CallbackQuery, doctor_id_text: str, session: AsyncSession
) -> None:
    """Render one doctor's profile and management actions."""
    try:
        doctor_id = int(doctor_id_text)
    except ValueError:
        await callback.message.answer(texts.DOCTOR_UNAVAILABLE)
        return
    doctor = await session.get(Doctor, doctor_id)
    if doctor is None:
        await callback.message.answer(texts.DOCTOR_UNAVAILABLE)
        return
    status = texts.ADMIN_DOCTOR_ACTIVE if doctor.is_active else texts.ADMIN_DOCTOR_INACTIVE
    await callback.message.edit_text(
        texts.ADMIN_DOCTOR_CARD.format(
            name=doctor.name, specialty=doctor.specialty, doctor_id=doctor.id, status=status
        ),
        reply_markup=admin_doctor_card_keyboard(doctor.id, doctor.is_active),
    )


@router.message(DoctorSetupStates.doctor_details)
async def receive_doctor_details(message: Message, state: FSMContext, settings: Settings) -> None:
    """Parse a new doctor name and specialty before schedule setup."""
    if not is_admin(message.from_user.id if message.from_user else None, settings):
        await state.clear()
        return
    if not message.text or "|" not in message.text:
        await message.answer(texts.ADMIN_INVALID_DOCTOR_DETAILS)
        return
    name, specialty = (part.strip() for part in message.text.split("|", maxsplit=1))
    if not name or not specialty or len(name) > 120 or len(specialty) > 120:
        await message.answer(texts.ADMIN_DOCTOR_DETAILS_LENGTH)
        return
    await state.update_data(name=name, specialty=specialty, schedules=[])
    await state.set_state(DoctorSetupStates.schedule_lines)
    await message.answer(texts.ADMIN_SCHEDULE_PROMPT)


@router.message(DoctorSetupStates.schedule_lines)
async def receive_schedule_line(
    message: Message, state: FSMContext, settings: Settings, session: AsyncSession
) -> None:
    """Collect weekly schedule rows and save the doctor when complete."""
    if not is_admin(message.from_user.id if message.from_user else None, settings):
        await state.clear()
        return
    if not message.text:
        await message.answer(texts.ADMIN_EXPECT_TEXT)
        return
    if message.text.strip().casefold() == texts.ADMIN_DONE_WORD:
        data = await state.get_data()
        schedules = [ScheduleWindow(**item) for item in data.get("schedules", [])]
        if not schedules:
            await message.answer(texts.ADMIN_SCHEDULE_EMPTY)
            return
        doctor = await add_doctor(session, data["name"], data["specialty"], schedules)
        await state.clear()
        await message.answer(
            texts.ADMIN_DOCTOR_CREATED.format(
                doctor_id=doctor.id, name=doctor.name, specialty=doctor.specialty
            )
        )
        return
    try:
        windows = parse_schedule_line(message.text)
        data = await state.get_data()
        existing = [ScheduleWindow(**item) for item in data.get("schedules", [])]
        validate_schedule([*existing, *windows])
    except ValueError as error:
        await message.answer(texts.ADMIN_SCHEDULE_REJECTED.format(reason=str(error)))
        return
    data["schedules"] = [*data.get("schedules", []), *[schedule_payload(item) for item in windows]]
    await state.set_data(data)
    await message.answer(
        texts.ADMIN_SCHEDULE_ADDED.format(days=", ".join(str(item.weekday + 1) for item in windows))
    )


@router.message(DoctorSetupStates.edit_name)
async def save_doctor_name(message: Message, state: FSMContext, session: AsyncSession) -> None:
    """Save a validated doctor name edit."""
    await save_doctor_field(message, state, session, "name")


@router.message(DoctorSetupStates.edit_specialty)
async def save_doctor_specialty(message: Message, state: FSMContext, session: AsyncSession) -> None:
    """Save a validated doctor specialty edit."""
    await save_doctor_field(message, state, session, "specialty")


async def save_doctor_field(
    message: Message, state: FSMContext, session: AsyncSession, field: str
) -> None:
    """Persist one doctor profile field and clear its dialog state."""
    data = await state.get_data()
    try:
        await update_doctor_details(session, int(data["doctor_id"]), **{field: message.text or ""})
    except (ValueError, KeyError) as error:
        await message.answer(str(error))
        return
    await state.clear()
    await message.answer(texts.ADMIN_DOCTOR_UPDATED)


@router.message(DoctorSetupStates.edit_schedule)
async def save_doctor_schedule(message: Message, state: FSMContext, session: AsyncSession) -> None:
    """Replace a doctor's schedule after validating all entered intervals."""
    data = await state.get_data()
    try:
        schedules = [
            parse_schedule_line(line) for line in (message.text or "").splitlines() if line.strip()
        ]
        flat_schedules = [window for group in schedules for window in group]
        await replace_doctor_schedule(session, int(data["doctor_id"]), flat_schedules)
    except (ValueError, KeyError) as error:
        await message.answer(str(error))
        return
    await state.clear()
    await message.answer(texts.ADMIN_SCHEDULE_CHANGED)


@router.message(Command("cancel"), DoctorSetupStates.doctor_details)
@router.message(Command("cancel"), DoctorSetupStates.schedule_lines)
@router.message(Command("cancel"), DoctorSetupStates.edit_name)
@router.message(Command("cancel"), DoctorSetupStates.edit_specialty)
@router.message(Command("cancel"), DoctorSetupStates.edit_schedule)
@router.message(Command("cancel"), AdminDayStates.closure_reason)
async def cancel_admin_dialog(message: Message, state: FSMContext) -> None:
    """Cancel an admin data-entry dialog."""
    await state.clear()
    await message.answer(texts.ADMIN_CANCEL_SETUP)


async def select_day_doctor(
    callback: CallbackQuery, doctor_id_text: str, state: FSMContext, timezone_name: str
) -> None:
    """Show the current-month calendar for the selected doctor."""
    await state.update_data(day_doctor_id=int(doctor_id_text), admin_action=None)
    today = datetime.now(ZoneInfo(timezone_name)).date()
    await callback.message.edit_text(
        texts.ADMIN_CHOOSE_DATE,
        reply_markup=admin_calendar_keyboard(today.year, today.month, today),
    )


async def update_admin_calendar(callback: CallbackQuery, value: str, timezone_name: str) -> None:
    """Change the displayed month without allowing dates in the past."""
    year_text, month_text, direction = value.split("-")
    year, month = int(year_text), int(month_text)
    if direction == "prev":
        year, month = (year - 1, 12) if month == 1 else (year, month - 1)
    else:
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)
    today = datetime.now(ZoneInfo(timezone_name)).date()
    if date(year, month, 1) < today.replace(day=1):
        year, month = today.year, today.month
    await callback.message.edit_reply_markup(
        reply_markup=admin_calendar_keyboard(year, month, today)
    )


async def render_day(
    callback: CallbackQuery,
    date_text: str,
    state: FSMContext,
    session: AsyncSession,
    timezone_name: str,
) -> None:
    """Render a clinic day using patient contact details and appointment comments."""
    data = await state.get_data()
    doctor_id = data.get("day_doctor_id")
    try:
        local_date = date.fromisoformat(date_text)
        doctor = await session.get(Doctor, int(doctor_id))
    except (ValueError, TypeError):
        doctor = None
    if doctor is None:
        await callback.message.answer(texts.DOCTOR_UNAVAILABLE)
        return
    records = await daily_appointments(session, doctor.id, local_date, timezone_name)
    if not records:
        text = texts.ADMIN_DAY_EMPTY.format(date=local_date.isoformat(), doctor=doctor.name)
    else:
        zone = ZoneInfo(timezone_name)
        lines = []
        for record in records:
            appointment = record.appointment
            local_start = appointment.starts_at.replace(tzinfo=UTC).astimezone(zone)
            confirmation = texts.CONFIRMATION_MARK if appointment.confirmed else ""
            lines.append(
                texts.ADMIN_DAY_ROW.format(
                    time=local_start.strftime("%H:%M"),
                    full_name=record.full_name or texts.ADMIN_PROFILE_MISSING,
                    phone=record.phone or "—",
                    appointment_id=appointment.id,
                    confirmed=confirmation,
                    reason=appointment.visit_reason or texts.ADMIN_REASON_EMPTY,
                    no_shows=record.no_show_count,
                    attendance=attendance_label(appointment.attendance_status),
                )
            )
        text = (
            texts.ADMIN_DAY_HEADER.format(
                doctor=doctor.name, date=local_date.strftime(texts.DATE_FORMAT)
            )
            + "\n\n"
            + "\n".join(lines)
        )
    await callback.message.edit_text(
        text,
        reply_markup=admin_day_keyboard(
            doctor.id, local_date, [row.appointment.id for row in records]
        ),
    )


async def handle_day_toggle(
    callback: CallbackQuery,
    value: str,
    action: str,
    state: FSMContext,
    session: AsyncSession,
    bot: Bot,
    timezone_name: str,
) -> None:
    """Ask for a closure reason or reopen the selected doctor's date."""
    doctor_text, date_text = value.split("|", maxsplit=1)
    doctor_id, local_date = int(doctor_text), date.fromisoformat(date_text)
    if action == "close_day":
        await state.update_data(closure_doctor_id=doctor_id, closure_date=local_date.isoformat())
        await state.set_state(AdminDayStates.closure_reason)
        if callback.message:
            await callback.message.answer(texts.ADMIN_REASON_PROMPT)
        return
    opened = await open_doctor_day(session, doctor_id, local_date)
    message = texts.ADMIN_DAY_OPENED if opened else texts.ADMIN_DAY_ALREADY_OPEN
    await callback.answer(message, show_alert=True)


@router.message(AdminDayStates.closure_reason)
async def receive_closure_reason(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
    settings: Settings,
    bot: Bot,
    timezone_name: str,
) -> None:
    """Close a doctor's date with the admin's reason and notify booked patients."""
    if not is_admin(message.from_user.id if message.from_user else None, settings):
        await state.clear()
        return
    reason = (message.text or "").strip()
    if not reason or len(reason) > 200:
        await message.answer(texts.ADMIN_REASON_PROMPT)
        return
    data = await state.get_data()
    try:
        local_date = date.fromisoformat(data["closure_date"])
        appointments = await close_doctor_day(
            session, int(data["closure_doctor_id"]), local_date, reason, timezone_name
        )
    except (KeyError, ValueError) as error:
        await message.answer(str(error))
        return
    failed_deliveries = 0
    for appointment in appointments:
        sent = await send_notice(
            bot,
            appointment.patient_telegram_id,
            texts.APPOINTMENT_CLOSED.format(
                appointment_id=appointment.id,
                date=local_date.strftime(texts.DATE_FORMAT),
                reason=reason,
            ),
        )
        failed_deliveries += not sent
    await state.clear()
    await message.answer(
        texts.ADMIN_DAY_CLOSED.format(cancelled=len(appointments), failed=failed_deliveries)
    )


async def handle_attendance(
    callback: CallbackQuery, appointment_text: str, action: str, session: AsyncSession
) -> None:
    """Persist a post-appointment attendance outcome."""
    outcome = AttendanceStatus.ATTENDED if action == "attended" else AttendanceStatus.NO_SHOW
    try:
        await mark_attendance(session, int(appointment_text), outcome)
    except ValueError as error:
        await callback.answer(str(error), show_alert=True)
        return
    await callback.answer(texts.ADMIN_ATTENDANCE_SAVED, show_alert=True)


def attendance_label(status: str) -> str:
    """Return the user-facing label for an attendance status."""
    labels = {
        AttendanceStatus.ATTENDED.value: texts.ADMIN_ATTENDED,
        AttendanceStatus.NO_SHOW.value: texts.ADMIN_NO_SHOW,
        AttendanceStatus.UNMARKED.value: texts.ADMIN_UNMARKED,
    }
    return labels.get(status, texts.ADMIN_UNMARKED)


def parse_schedule_line(value: str) -> list[ScheduleWindow]:
    """Parse a schedule row formatted as weekdays, start, end, and duration."""
    parts = value.split()
    if len(parts) != 4:
        raise ValueError(
            texts.ADMIN_SCHEDULE_REJECTED.format(reason=texts.ADMIN_SCHEDULE_VALUE_COUNT)
        )
    day_text, start_time, end_time, duration_text = parts
    try:
        weekdays = [int(day) - 1 for day in day_text.split(",")]
        duration = int(duration_text)
    except ValueError as error:
        raise ValueError(
            texts.ADMIN_SCHEDULE_REJECTED.format(reason=texts.ADMIN_SCHEDULE_NUMBERS_INVALID)
        ) from error
    if not weekdays or any(day < 0 or day > 6 for day in weekdays):
        raise ValueError(
            texts.ADMIN_SCHEDULE_REJECTED.format(reason=texts.ADMIN_SCHEDULE_WEEKDAYS_INVALID)
        )
    windows = [ScheduleWindow(day, start_time, end_time, duration) for day in weekdays]
    validate_schedule(windows)
    return windows


def schedule_payload(window: ScheduleWindow) -> dict[str, int | str]:
    """Convert a schedule window to JSON-safe FSM data."""
    return {
        "weekday": window.weekday,
        "start_time": window.start_time,
        "end_time": window.end_time,
        "duration_minutes": window.duration_minutes,
    }
