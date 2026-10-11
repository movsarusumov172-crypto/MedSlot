"""Admin-only commands for doctor setup, daily schedule and closures."""

from datetime import UTC, date
from zoneinfo import ZoneInfo

from aiogram import Bot, Router
from aiogram.filters import Command, CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.types import Message
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from medslot.booking import add_doctor, appointments_for_day, close_doctor_day
from medslot.config import Settings
from medslot.delivery import send_notice
from medslot.models import Doctor
from medslot.scheduling import ScheduleWindow, validate_schedule
from medslot.states import DoctorSetupStates

router = Router(name="admin")


def is_admin(message: Message, settings: Settings) -> bool:
    """Check the configured numeric Telegram IDs at action time."""
    return message.from_user is not None and message.from_user.id in settings.admin_ids


@router.message(Command("admin"))
async def admin_help(message: Message, settings: Settings) -> None:
    """Show admin actions only to configured admins."""
    if not is_admin(message, settings):
        return
    await message.answer(
        "Администрирование:\n"
        "/add_doctor — добавить врача и недельный график\n"
        "/doctors — список врачей с ID\n"
        "/day YYYY-MM-DD ID — записи врача на день\n"
        "/close_day YYYY-MM-DD ID причина — закрыть день врача"
    )


@router.message(Command("add_doctor"))
async def begin_doctor_setup(message: Message, state: FSMContext, settings: Settings) -> None:
    """Start the admin's doctor profile and schedule form."""
    if not is_admin(message, settings):
        return
    await state.clear()
    await state.set_state(DoctorSetupStates.doctor_details)
    await message.answer("Введите имя и специальность одной строкой: Имя | Специальность")


@router.message(DoctorSetupStates.doctor_details)
async def receive_doctor_details(message: Message, state: FSMContext, settings: Settings) -> None:
    """Validate doctor profile details before accepting schedule lines."""
    if not is_admin(message, settings):
        await state.clear()
        return
    if not message.text or "|" not in message.text:
        await message.answer("Формат: Имя | Специальность. Попробуйте ещё раз или /cancel.")
        return
    name, specialty = (part.strip() for part in message.text.split("|", maxsplit=1))
    if not name or not specialty or len(name) > 120 or len(specialty) > 120:
        await message.answer("Имя и специальность должны быть от 1 до 120 символов.")
        return
    await state.update_data(name=name, specialty=specialty, schedules=[])
    await state.set_state(DoctorSetupStates.schedule_lines)
    await message.answer(
        "Добавьте график строками: дни HH:MM HH:MM длительность_минут. "
        "Дни: 1=Пн ... 7=Вс, пример: 1,3,5 09:00 17:00 30. "
        "Напишите «готово» после ввода графика."
    )


@router.message(DoctorSetupStates.schedule_lines)
async def receive_schedule_line(
    message: Message,
    state: FSMContext,
    settings: Settings,
    session: AsyncSession,
) -> None:
    """Add one recurring schedule line or save the completed doctor."""
    if not is_admin(message, settings):
        await state.clear()
        return
    if not message.text:
        await message.answer("Ожидается текстовая строка графика.")
        return
    if message.text.strip().casefold() == "готово":
        await finish_doctor_setup(message, state, session)
        return

    try:
        weekdays = await append_schedule_line(state, message.text)
    except ValueError as error:
        await message.answer(f"Строка не принята: {error}. Пример: 1,3,5 09:00 17:00 30")
        return

    await message.answer(f"Добавлены дни: {', '.join(str(day + 1) for day in weekdays)}.")


async def finish_doctor_setup(message: Message, state: FSMContext, session: AsyncSession) -> None:
    """Validate and persist a complete doctor profile and weekly schedule."""
    data = await state.get_data()
    schedules = [ScheduleWindow(**item) for item in data.get("schedules", [])]
    if not schedules:
        await message.answer("Добавьте хотя бы одну строку графика.")
        return
    try:
        doctor = await add_doctor(session, data["name"], data["specialty"], schedules)
    except ValueError as error:
        await message.answer(f"График не сохранён: {error}. Исправьте строки.")
        return
    await state.clear()
    await message.answer(f"Врач добавлен. ID: {doctor.id}; {doctor.name} — {doctor.specialty}.")


async def append_schedule_line(state: FSMContext, text: str) -> list[int]:
    """Parse one admin schedule row and append its non-overlapping intervals."""
    parts = text.split()
    if len(parts) != 4:
        raise ValueError("нужно 4 значения: дни, начало, конец, длительность")
    day_text, start_time, end_time, duration_text = parts
    try:
        weekdays = [int(value) - 1 for value in day_text.split(",")]
        duration = int(duration_text)
    except ValueError as error:
        raise ValueError("дни и длительность задаются целыми числами") from error
    if not weekdays or any(not 0 <= day <= 6 for day in weekdays):
        raise ValueError("дни недели задаются числами от 1 до 7")
    new_windows = [ScheduleWindow(day, start_time, end_time, duration) for day in weekdays]
    data = await state.get_data()
    existing = [ScheduleWindow(**item) for item in data.get("schedules", [])]
    validate_schedule([*existing, *new_windows])
    data["schedules"] = [
        *data.get("schedules", []),
        *[
            {
                "weekday": window.weekday,
                "start_time": window.start_time,
                "end_time": window.end_time,
                "duration_minutes": window.duration_minutes,
            }
            for window in new_windows
        ],
    ]
    await state.set_data(data)
    return weekdays


@router.message(Command("cancel"), DoctorSetupStates.doctor_details)
@router.message(Command("cancel"), DoctorSetupStates.schedule_lines)
async def cancel_doctor_setup(message: Message, state: FSMContext, settings: Settings) -> None:
    """Let an admin cancel the doctor setup dialog."""
    if not is_admin(message, settings):
        return
    await state.clear()
    await message.answer("Добавление врача отменено.")


@router.message(Command("doctors"))
async def list_doctors(message: Message, session: AsyncSession, settings: Settings) -> None:
    """List doctor IDs required by the day-report and closure commands."""
    if not is_admin(message, settings):
        return
    doctors = list(await session.scalars(select(Doctor).where(Doctor.is_active.is_(True))))
    if not doctors:
        await message.answer("Врачей пока нет.")
        return
    await message.answer(
        "Врачи:\n" + "\n".join(f"ID {doc.id}: {doc.name} — {doc.specialty}" for doc in doctors)
    )


@router.message(Command("day"))
async def show_day(
    message: Message,
    command: CommandObject,
    session: AsyncSession,
    settings: Settings,
    timezone_name: str,
) -> None:
    """Show one doctor's booked appointments on a clinic-local date."""
    if not is_admin(message, settings):
        return
    date_text, doctor_text = _parse_day_args(command.args)
    if date_text is None or doctor_text is None:
        await message.answer("Формат: /day YYYY-MM-DD ID")
        return
    try:
        local_date = date.fromisoformat(date_text)
        doctor_id = int(doctor_text)
        appointments = await appointments_for_day(session, doctor_id, local_date, timezone_name)
    except (ValueError, TypeError):
        await message.answer("Проверьте дату и ID врача. Формат: /day YYYY-MM-DD ID")
        return
    if not appointments:
        await message.answer(f"Записей на {local_date.isoformat()} нет.")
        return
    zone = ZoneInfo(timezone_name)
    lines = [
        f"{item.starts_at.replace(tzinfo=UTC).astimezone(zone):%H:%M} — "
        f"запись #{item.id}, Telegram ID {item.patient_telegram_id}"
        for item in appointments
    ]
    await message.answer(f"Записи на {local_date.isoformat()}:\n" + "\n".join(lines))


@router.message(Command("close_day"))
async def close_day(
    message: Message,
    command: CommandObject,
    session: AsyncSession,
    settings: Settings,
    bot: Bot,
    timezone_name: str,
) -> None:
    """Close a doctor's local day and notify affected patients."""
    if not is_admin(message, settings):
        return
    parts = (command.args or "").split(maxsplit=2)
    if len(parts) < 2:
        await message.answer("Формат: /close_day YYYY-MM-DD ID причина")
        return
    try:
        local_date = date.fromisoformat(parts[0])
        doctor_id = int(parts[1])
    except ValueError:
        await message.answer("Проверьте дату и ID врача. Формат: /close_day YYYY-MM-DD ID причина")
        return
    reason = parts[2] if len(parts) == 3 else "День закрыт"
    try:
        affected = await close_doctor_day(session, doctor_id, local_date, reason, timezone_name)
    except (ValueError, TypeError) as error:
        await message.answer(f"Не удалось закрыть день: {error}")
        return
    failed_deliveries = 0
    for appointment in affected:
        sent = await send_notice(
            bot,
            appointment.patient_telegram_id,
            f"Приём #{appointment.id} на {local_date:%d.%m.%Y} отменён. Причина: {reason}.",
        )
        failed_deliveries += not sent
    await message.answer(
        f"День закрыт. Записей отменено: {len(affected)}. "
        f"Не удалось доставить уведомления: {failed_deliveries}."
    )


def _parse_day_args(arguments: str | None) -> tuple[str | None, str | None]:
    if not arguments:
        return None, None
    parts = arguments.split()
    if len(parts) != 2:
        return None, None
    return parts[0], parts[1]
