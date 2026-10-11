"""Tests for the two-hour appointment cancellation rule."""

from datetime import UTC, datetime, timedelta

from medslot.booking import can_cancel


def test_cancellation_is_allowed_at_exactly_two_hours() -> None:
    """The cutoff is inclusive at exactly two hours before the appointment."""
    now = datetime(2030, 1, 1, 10, 0, tzinfo=UTC)

    assert can_cancel(now + timedelta(hours=2), now)


def test_cancellation_is_rejected_inside_two_hour_window() -> None:
    """A start time less than two hours away cannot be cancelled."""
    now = datetime(2030, 1, 1, 10, 0, tzinfo=UTC)

    assert not can_cancel(now + timedelta(hours=1, minutes=59), now)


def test_cancellation_is_rejected_after_appointment_start() -> None:
    """Past appointments cannot be cancelled."""
    now = datetime(2030, 1, 1, 10, 0, tzinfo=UTC)

    assert not can_cancel(now - timedelta(minutes=1), now)
