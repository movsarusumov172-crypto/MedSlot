"""Inline keyboards and compact callback payloads for patient booking."""

from calendar import monthrange
from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

from aiogram.filters.callback_data import CallbackData
from aiogram.types import InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from medslot import texts
from medslot.models import Doctor
from medslot.scheduling import Slot


class BookingCb(CallbackData, prefix="bk"):
    """Callback data for the short patient booking flow."""

    action: str
    value: str


class ProfileCb(CallbackData, prefix="pr"):
    """Actions on the patient's own profile."""

    action: str


class AdminCb(CallbackData, prefix="ad"):
    """Admin menu, doctor and day-view actions."""

    action: str
    value: str = ""


def choices_keyboard(values: list[tuple[str, str]], action: str) -> InlineKeyboardMarkup:
    """Build one button per selection, with validated IDs or names in callback data."""
    builder = InlineKeyboardBuilder()
    for label, value in values:
        builder.button(text=label, callback_data=BookingCb(action=action, value=value))
    builder.adjust(1)
    return builder.as_markup()


def calendar_keyboard(year: int, month: int, today: date) -> InlineKeyboardMarkup:
    """Build a month calendar with navigation and no past dates."""
    builder = InlineKeyboardBuilder()
    builder.button(
        text="‹", callback_data=BookingCb(action="month", value=f"{year}-{month:02d}-prev")
    )
    builder.button(text=f"{year}-{month:02d}", callback_data=BookingCb(action="noop", value=""))
    builder.button(
        text="›", callback_data=BookingCb(action="month", value=f"{year}-{month:02d}-next")
    )
    for label in texts.WEEKDAY_LABELS:
        builder.button(text=label, callback_data=BookingCb(action="noop", value=""))
    first_weekday, days_in_month = monthrange(year, month)
    for _ in range(first_weekday):
        builder.button(text="·", callback_data=BookingCb(action="noop", value=""))
    for day in range(1, days_in_month + 1):
        selected = date(year, month, day)
        if selected < today:
            builder.button(text="·", callback_data=BookingCb(action="noop", value=""))
        else:
            builder.button(
                text=str(day),
                callback_data=BookingCb(action="day", value=selected.isoformat()),
            )
    builder.adjust(3, 7, 7)
    return builder.as_markup()


def appointments_keyboard(appointment_ids: list[int]) -> InlineKeyboardMarkup:
    """Build per-appointment move and cancellation buttons."""
    builder = InlineKeyboardBuilder()
    for appointment_id in appointment_ids:
        builder.button(
            text=f"{texts.MOVE_BOOKING_BUTTON} #{appointment_id}",
            callback_data=BookingCb(action="move", value=str(appointment_id)),
        )
        builder.button(
            text=f"{texts.CANCEL_BOOKING_BUTTON} #{appointment_id}",
            callback_data=BookingCb(action="cancel", value=str(appointment_id)),
        )
    builder.adjust(2)
    return builder.as_markup()


def profile_keyboard() -> InlineKeyboardMarkup:
    """Offer the two supported patient profile edits."""
    builder = InlineKeyboardBuilder()
    builder.button(text=texts.PROFILE_EDIT_NAME, callback_data=ProfileCb(action="name"))
    builder.button(text=texts.PROFILE_EDIT_PHONE, callback_data=ProfileCb(action="phone"))
    builder.adjust(1)
    return builder.as_markup()


def consent_keyboard() -> InlineKeyboardMarkup:
    """Build the distinct affirmative and negative consent choices."""
    builder = InlineKeyboardBuilder()
    builder.button(text=texts.CONSENT_ACCEPT, callback_data=ProfileCb(action="agree"))
    builder.button(text=texts.CONSENT_DECLINE, callback_data=ProfileCb(action="decline"))
    builder.adjust(2)
    return builder.as_markup()


def reminder_keyboard(appointment_id: int) -> InlineKeyboardMarkup:
    """Build confirmation, move, and cancellation actions for a reminder."""
    builder = InlineKeyboardBuilder()
    builder.button(
        text=texts.REMINDER_KEYBOARD[0],
        callback_data=BookingCb(action="confirm", value=str(appointment_id)),
    )
    builder.button(
        text=texts.REMINDER_KEYBOARD[1],
        callback_data=BookingCb(action="move", value=str(appointment_id)),
    )
    builder.button(
        text=texts.REMINDER_KEYBOARD[2],
        callback_data=BookingCb(action="cancel", value=str(appointment_id)),
    )
    builder.adjust(1, 2)
    return builder.as_markup()


def admin_menu_keyboard() -> InlineKeyboardMarkup:
    """Build the clinic admin menu."""
    builder = InlineKeyboardBuilder()
    for label, action in (
        (texts.ADMIN_DOCTORS, "doctors"),
        (texts.ADMIN_ADD_DOCTOR, "add_doctor"),
        (texts.ADMIN_DAY_SCHEDULE, "day_doctor"),
    ):
        builder.button(text=label, callback_data=AdminCb(action=action))
    builder.adjust(1)
    return builder.as_markup()


def admin_doctors_keyboard(doctors: list[Doctor]) -> InlineKeyboardMarkup:
    """Build a doctor picker using database IDs only in callback payloads."""
    builder = InlineKeyboardBuilder()
    for doctor in doctors:
        builder.button(
            text=f"{doctor.name} — {doctor.specialty}",
            callback_data=AdminCb(action="doctor", value=str(doctor.id)),
        )
    builder.button(text=texts.ADMIN_MENU, callback_data=AdminCb(action="menu"))
    builder.adjust(1)
    return builder.as_markup()


def admin_doctor_card_keyboard(doctor_id: int, is_active: bool) -> InlineKeyboardMarkup:
    """Build editing and activation actions for one doctor."""
    builder = InlineKeyboardBuilder()
    actions = (
        (texts.ADMIN_EDIT_NAME, "edit_name"),
        (texts.ADMIN_EDIT_SPECIALTY, "edit_specialty"),
        (texts.ADMIN_EDIT_SCHEDULE, "edit_schedule"),
    )
    for label, action in actions:
        builder.button(text=label, callback_data=AdminCb(action=action, value=str(doctor_id)))
    label = texts.ADMIN_DEACTIVATE if is_active else texts.ADMIN_ACTIVATE
    builder.button(text=label, callback_data=AdminCb(action="toggle", value=str(doctor_id)))
    builder.button(text=texts.ADMIN_DOCTORS, callback_data=AdminCb(action="doctors"))
    builder.adjust(1)
    return builder.as_markup()


def admin_day_keyboard(
    doctor_id: int, local_date: date, appointment_ids: list[int]
) -> InlineKeyboardMarkup:
    """Build day close/open controls and attendance actions."""
    builder = InlineKeyboardBuilder()
    for appointment_id in appointment_ids:
        builder.button(
            text=f"{texts.ADMIN_MARK_ATTENDED} #{appointment_id}",
            callback_data=AdminCb(action="attended", value=str(appointment_id)),
        )
        builder.button(
            text=f"{texts.ADMIN_MARK_NO_SHOW} #{appointment_id}",
            callback_data=AdminCb(action="no_show", value=str(appointment_id)),
        )
    builder.button(
        text=texts.ADMIN_CLOSE_DAY,
        callback_data=AdminCb(action="close_day", value=f"{doctor_id}|{local_date.isoformat()}"),
    )
    builder.button(
        text=texts.ADMIN_OPEN_DAY,
        callback_data=AdminCb(action="open_day", value=f"{doctor_id}|{local_date.isoformat()}"),
    )
    builder.button(text=texts.ADMIN_MENU, callback_data=AdminCb(action="menu"))
    builder.adjust(2)
    return builder.as_markup()


def admin_calendar_keyboard(year: int, month: int, today: date) -> InlineKeyboardMarkup:
    """Build a date calendar for viewing one doctor's schedule."""
    builder = InlineKeyboardBuilder()
    builder.button(
        text="‹", callback_data=AdminCb(action="calendar_month", value=f"{year}-{month:02d}-prev")
    )
    builder.button(text=f"{year}-{month:02d}", callback_data=AdminCb(action="noop"))
    builder.button(
        text="›", callback_data=AdminCb(action="calendar_month", value=f"{year}-{month:02d}-next")
    )
    for label in texts.WEEKDAY_LABELS:
        builder.button(text=label, callback_data=AdminCb(action="noop"))
    first_weekday, days_in_month = monthrange(year, month)
    for _ in range(first_weekday):
        builder.button(text="·", callback_data=AdminCb(action="noop"))
    for day in range(1, days_in_month + 1):
        selected = date(year, month, day)
        if selected < today:
            builder.button(text="·", callback_data=AdminCb(action="noop"))
        else:
            builder.button(
                text=str(day), callback_data=AdminCb(action="day", value=selected.isoformat())
            )
    builder.adjust(3, 7, 7)
    return builder.as_markup()


def contact_keyboard() -> ReplyKeyboardMarkup:
    """Offer Telegram's contact sharing and keep manual phone input available."""
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text=texts.SHARE_PHONE_BUTTON, request_contact=True)]],
        resize_keyboard=True,
        one_time_keyboard=True,
    )


def slots_keyboard(doctor_id: int, slots: list[Slot], zone: ZoneInfo) -> InlineKeyboardMarkup:
    """Build one button per free slot, labelled with the clinic-local start time."""
    # ":" separates callback data fields in aiogram, so it cannot appear in a value.
    values = [
        (
            slot.starts_at.astimezone(zone).strftime("%H:%M"),
            f"{doctor_id}-{int(slot.starts_at.timestamp())}",
        )
        for slot in slots
    ]
    return choices_keyboard(values, "slot")


def parse_slot_value(value: str) -> tuple[int, datetime]:
    """Decode a slot button value back into the doctor ID and the UTC start time."""
    try:
        doctor_text, timestamp_text = value.split("-", maxsplit=1)
        return int(doctor_text), datetime.fromtimestamp(int(timestamp_text), UTC)
    except (ValueError, OverflowError, OSError) as error:
        raise ValueError(texts.INVALID_SLOT) from error
