"""Start the MedSlot Telegram bot."""

import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage

from medslot.admin import router as admin_router
from medslot.appointments import router as appointments_router
from medslot.config import get_settings
from medslot.database import create_engine, create_session_factory, initialize_database
from medslot.middleware import DatabaseSessionMiddleware, LoggingErrorMiddleware
from medslot.patient import router as patient_router
from medslot.reminders import create_scheduler


async def main() -> None:
    """Initialize persistence, scheduler and long polling with graceful cleanup."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    settings = get_settings()
    engine = create_engine(settings)
    session_factory = create_session_factory(engine)
    await initialize_database(engine)

    bot = Bot(token=settings.bot_token.get_secret_value())
    dispatcher = Dispatcher(storage=MemoryStorage())
    dispatcher["settings"] = settings
    dispatcher["timezone_name"] = settings.timezone
    dispatcher.update.outer_middleware(LoggingErrorMiddleware())
    dispatcher.update.outer_middleware(DatabaseSessionMiddleware(session_factory))
    dispatcher.include_routers(patient_router, appointments_router, admin_router)

    scheduler = create_scheduler(session_factory, bot, settings.timezone)
    scheduler.start()
    try:
        await dispatcher.start_polling(
            bot,
            allowed_updates=dispatcher.resolve_used_update_types(),
            settings=settings,
            timezone_name=settings.timezone,
        )
    finally:
        scheduler.shutdown(wait=False)
        await bot.session.close()
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
