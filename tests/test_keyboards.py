"""Booking buttons must stay within Telegram's callback data rules."""

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from medslot.keyboards import choices_keyboard, parse_slot_value, slots_keyboard
from medslot.scheduling import Slot

TELEGRAM_CALLBACK_LIMIT = 64


def test_slot_buttons_round_trip_doctor_and_start_time() -> None:
    """A slot button decodes back to the exact doctor and UTC start it was built from."""
    start = datetime(2030, 1, 7, 9, 30, tzinfo=UTC)
    slot = Slot(starts_at=start, ends_at=start + timedelta(minutes=30))

    keyboard = slots_keyboard(42, [slot], ZoneInfo("Europe/Tallinn"))

    button = keyboard.inline_keyboard[0][0]
    assert button.text == "11:30"
    assert len(button.callback_data.encode()) <= TELEGRAM_CALLBACK_LIMIT
    value = button.callback_data.split(":", maxsplit=2)[2]
    assert parse_slot_value(value) == (42, start)


def test_tampered_slot_value_is_rejected_with_a_readable_error() -> None:
    with pytest.raises(ValueError, match="Неверный слот"):
        parse_slot_value("not-a-slot")


def test_specialty_buttons_fit_for_long_cyrillic_names() -> None:
    """Specialty buttons carry an index, so name length never breaks the keyboard."""
    keyboard = choices_keyboard([("Оториноларинголог-офтальмолог детский" * 3, "0")], "specialty")

    button = keyboard.inline_keyboard[0][0]
    assert len(button.callback_data.encode()) <= TELEGRAM_CALLBACK_LIMIT
