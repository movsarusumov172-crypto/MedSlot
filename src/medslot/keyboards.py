"""Inline keyboards and compact callback payloads for patient booking."""

from calendar import monthrange
from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

from aiogram.filters.callback_data import CallbackData
from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from medslot.scheduling import Slot


class BookingCb(CallbackData, prefix="bk"):
    """Callback data for the short patient booking flow."""

    action: str
    value: str


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
    for label in ("Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"):
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
    """Build cancellation buttons for the patient's active appointments."""
    builder = InlineKeyboardBuilder()
    for appointment_id in appointment_ids:
        builder.button(
            text=f"Отменить запись #{appointment_id}",
            callback_data=BookingCb(action="cancel", value=str(appointment_id)),
        )
    builder.adjust(1)
    return builder.as_markup()


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
        raise ValueError("Неверный слот, начните запись заново: /book") from error
