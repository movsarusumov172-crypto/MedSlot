"""Bounded Telegram delivery for reminders and appointment closure notices."""

import asyncio
import logging

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError, TelegramRetryAfter

logger = logging.getLogger(__name__)
MAX_SEND_ATTEMPTS = 3


async def send_notice(bot: Bot, chat_id: int, text: str) -> bool:
    """Retry rate-limited sends and report permanent delivery failures."""
    for attempt in range(MAX_SEND_ATTEMPTS):
        try:
            await bot.send_message(chat_id, text)
            return True
        except TelegramRetryAfter as error:
            if attempt + 1 == MAX_SEND_ATTEMPTS:
                logger.warning("Telegram kept rate limiting chat %s", chat_id)
                return False
            await asyncio.sleep(error.retry_after)
        except TelegramForbiddenError:
            logger.info("Telegram chat %s cannot receive messages", chat_id)
            return False
        except TelegramBadRequest as error:
            logger.info("Telegram rejected a message for chat %s: %s", chat_id, error.message)
            return False
    return False
