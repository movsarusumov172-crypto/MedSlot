"""Per-update database session and centralized logging/error handling."""

import logging
from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, TelegramObject, Update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from medslot import texts

logger = logging.getLogger(__name__)


class DatabaseSessionMiddleware(BaseMiddleware):
    """Inject a request-scoped session and roll back unfinished transactions."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self.session_factory = session_factory

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:  # noqa: ANN401 - aiogram's middleware contract returns Any
        async with self.session_factory() as session:
            data["session"] = session
            return await handler(event, data)


class LoggingErrorMiddleware(BaseMiddleware):
    """Log unexpected update errors and return a neutral user-facing response."""

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:  # noqa: ANN401 - aiogram's middleware contract returns Any
        update = data.get("event_update")
        if isinstance(update, Update):
            user = update.event_from_user
            logger.info(
                "Handling Telegram update %s from user %s",
                update.update_id,
                user.id if user else None,
            )
        try:
            return await handler(event, data)
        except Exception:
            user_id = (
                update.event_from_user.id
                if isinstance(update, Update) and update.event_from_user
                else None
            )
            logger.exception("Update processing failed for Telegram user %s", user_id)
            if isinstance(update, Update) and update.message:
                await update.message.answer(texts.ERROR_GENERIC)
            elif isinstance(update, Update) and update.callback_query:
                callback = update.callback_query
                if isinstance(callback, CallbackQuery):
                    await callback.answer(texts.ERROR_GENERIC, show_alert=True)
            return None
