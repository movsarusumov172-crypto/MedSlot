"""Validated environment configuration."""

from functools import lru_cache
from typing import Annotated
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import SecretStr, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime settings loaded from environment variables or .env."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    bot_token: SecretStr
    admin_ids: Annotated[list[int], NoDecode]
    database_url: str = "sqlite+aiosqlite:///./data/medslot.db"
    timezone: str = "Europe/Tallinn"

    @field_validator("admin_ids", mode="before")
    @classmethod
    def parse_admin_ids(cls, value: object) -> object:
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        return value

    @field_validator("admin_ids")
    @classmethod
    def require_admin_ids(cls, value: list[int]) -> list[int]:
        if not value or any(admin_id <= 0 for admin_id in value):
            raise ValueError("ADMIN_IDS must contain positive Telegram user IDs")
        return value

    @field_validator("timezone")
    @classmethod
    def validate_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError as error:
            raise ValueError(f"Unknown timezone: {value}") from error
        return value


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Load settings once for this process."""
    return Settings()
