"""Tests for appointment slot generation and recurring schedule validation."""

from datetime import UTC, date, datetime

import pytest

from medslot.scheduling import ScheduleWindow, generate_slots, validate_schedule


def test_slots_fit_whole_appointments_inside_work_window() -> None:
    """Only complete 30-minute appointments are generated before closing."""
    windows = [ScheduleWindow(weekday=0, start_time="09:00", end_time="10:20", duration_minutes=30)]

    slots = generate_slots(
        date(2030, 1, 7),
        windows,
        "UTC",
        now=datetime(2030, 1, 1, tzinfo=UTC),
    )

    assert [slot.starts_at.strftime("%H:%M") for slot in slots] == ["09:00", "09:30"]
    assert [slot.ends_at.strftime("%H:%M") for slot in slots] == ["09:30", "10:00"]


def test_slot_generation_skips_past_start_times() -> None:
    """Slots earlier than the current instant are unavailable."""
    slots = generate_slots(
        date(2030, 1, 7),
        [ScheduleWindow(0, "09:00", "10:00", 30)],
        "UTC",
        now=datetime(2030, 1, 7, 9, 1, tzinfo=UTC),
    )

    assert [slot.starts_at.strftime("%H:%M") for slot in slots] == ["09:30"]


def test_overlapping_work_windows_are_rejected() -> None:
    """A doctor cannot have overlapping recurring work intervals on one weekday."""
    windows = [
        ScheduleWindow(0, "09:00", "12:00", 30),
        ScheduleWindow(0, "11:30", "15:00", 30),
    ]

    with pytest.raises(ValueError, match="пересекаются"):
        validate_schedule(windows)


def test_adjacent_work_windows_are_allowed() -> None:
    """Back-to-back intervals do not overlap."""
    validate_schedule(
        [ScheduleWindow(0, "09:00", "12:00", 30), ScheduleWindow(0, "12:00", "15:00", 30)]
    )
