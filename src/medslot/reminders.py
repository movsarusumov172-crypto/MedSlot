"""APScheduler job that delivers persisted appointment reminders."""

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from aiogram import Bot
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import joinedload

from medslot import texts
from medslot.booking import as_utc
from medslot.delivery import send_notice
from medslot.keyboards import reminder_keyboard
from medslot.models import Appointment, AppointmentStatus

REMINDER_INTERVAL_SECONDS = 60


async def deliver_due_reminders(
    session_factory: async_sessionmaker[AsyncSession],
    bot: Bot,
    timezone_name: str,
    now: datetime | None = None,
) -> None:
    """Send due reminders and persist successful delivery flags."""
    current_time = as_utc(now or datetime.now(UTC))
    async with session_factory() as session:
        appointments = list(
            await session.scalars(
                select(Appointment)
                .options(joinedload(Appointment.doctor))
                .where(
                    Appointment.status == AppointmentStatus.BOOKED.value,
                    Appointment.starts_at > current_time.replace(tzinfo=None),
                    (
                        (
                            Appointment.reminder_24h_sent.is_(False)
                            & (
                                Appointment.starts_at
                                <= (current_time + timedelta(hours=24)).replace(tzinfo=None)
                            )
                        )
                        | (
                            Appointment.reminder_2h_sent.is_(False)
                            & (
                                Appointment.starts_at
                                <= (current_time + timedelta(hours=2)).replace(tzinfo=None)
                            )
                        )
                    ),
                )
            )
        )
        local_zone = ZoneInfo(timezone_name)
        for appointment in appointments:
            starts_at = as_utc(appointment.starts_at)
            local_start = starts_at.astimezone(local_zone).strftime(texts.LOCAL_DATETIME_FORMAT)
            for hours, field_name in ((24, "reminder_24h_sent"), (2, "reminder_2h_sent")):
                due = starts_at - current_time <= timedelta(hours=hours) and as_utc(
                    appointment.created_at
                ) <= starts_at - timedelta(hours=hours)
                if not due or getattr(appointment, field_name):
                    continue
                text = texts.REMINDER.format(
                    doctor=appointment.doctor.name, when=local_start, hours=hours
                )
                sent = await send_notice(
                    bot,
                    appointment.patient_telegram_id,
                    text,
                    reply_markup=reminder_keyboard(appointment.id),
                )
                if not sent:
                    continue
                setattr(appointment, field_name, True)
                await session.commit()


def create_scheduler(
    session_factory: async_sessionmaker[AsyncSession], bot: Bot, timezone_name: str
) -> AsyncIOScheduler:
    """Create the recurring due-reminder scan."""
    scheduler = AsyncIOScheduler(timezone=ZoneInfo(timezone_name))
    scheduler.add_job(
        deliver_due_reminders,
        trigger="interval",
        seconds=REMINDER_INTERVAL_SECONDS,
        args=[session_factory, bot, timezone_name],
        id="appointment-reminders",
        replace_existing=True,
        coalesce=True,
        max_instances=1,
    )
    return scheduler
