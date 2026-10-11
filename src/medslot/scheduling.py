"""Schedule validation and generation of local appointment slots."""

from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from itertools import pairwise
from zoneinfo import ZoneInfo

from medslot import texts


@dataclass(frozen=True, slots=True)
class ScheduleWindow:
    """One recurring working interval and its appointment length."""

    weekday: int
    start_time: str
    end_time: str
    duration_minutes: int


@dataclass(frozen=True, slots=True)
class Slot:
    """A slot represented in UTC for stable storage and comparison."""

    starts_at: datetime
    ends_at: datetime


def parse_clock(value: str) -> time:
    """Parse a strict 24-hour HH:MM clock value."""
    try:
        parsed = time.fromisoformat(value)
    except ValueError as error:
        raise ValueError(texts.SCHEDULE_TIME_FORMAT) from error
    if parsed.second or parsed.microsecond or len(value) != 5:
        raise ValueError(texts.SCHEDULE_TIME_FORMAT)
    return parsed


def validate_schedule(windows: list[ScheduleWindow]) -> None:
    """Reject invalid and overlapping recurring work intervals."""
    intervals: dict[int, list[tuple[time, time]]] = {}
    for window in windows:
        if not 0 <= window.weekday <= 6:
            raise ValueError(texts.SCHEDULE_WEEKDAY_RANGE)
        if window.duration_minutes <= 0:
            raise ValueError(texts.SCHEDULE_DURATION_POSITIVE)
        start = parse_clock(window.start_time)
        end = parse_clock(window.end_time)
        if start >= end:
            raise ValueError(texts.SCHEDULE_END_AFTER_START)
        interval_minutes = (end.hour * 60 + end.minute) - (start.hour * 60 + start.minute)
        if window.duration_minutes > interval_minutes:
            raise ValueError(texts.SCHEDULE_DURATION_FITS)
        intervals.setdefault(window.weekday, []).append((start, end))

    for weekday_intervals in intervals.values():
        weekday_intervals.sort()
        for previous, current in pairwise(weekday_intervals):
            if current[0] < previous[1]:
                raise ValueError(texts.SCHEDULE_OVERLAP)


def generate_slots(
    local_date: date,
    windows: list[ScheduleWindow],
    timezone_name: str,
    now: datetime | None = None,
) -> list[Slot]:
    """Generate full appointments that fit today's recurring work windows."""
    local_zone = ZoneInfo(timezone_name)
    current_time = now or datetime.now(UTC)
    if current_time.tzinfo is None:
        current_time = current_time.replace(tzinfo=UTC)
    current_time = current_time.astimezone(UTC)
    slots: list[Slot] = []

    for window in windows:
        if window.weekday != local_date.weekday():
            continue
        start_clock = parse_clock(window.start_time)
        end_clock = parse_clock(window.end_time)
        cursor = datetime.combine(local_date, start_clock, tzinfo=local_zone)
        closing_time = datetime.combine(local_date, end_clock, tzinfo=local_zone)
        duration = timedelta(minutes=window.duration_minutes)

        while cursor + duration <= closing_time:
            slot_end = cursor + duration
            starts_at = cursor.astimezone(UTC)
            ends_at = slot_end.astimezone(UTC)
            if starts_at > current_time:
                slots.append(Slot(starts_at=starts_at, ends_at=ends_at))
            cursor = slot_end

    return sorted(slots, key=lambda slot: slot.starts_at)
