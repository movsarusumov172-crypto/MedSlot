"""Async SQLite engine and session lifecycle."""

from pathlib import Path
from typing import Protocol

from sqlalchemy import event
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from medslot.config import Settings


class SQLiteCursor(Protocol):
    """Minimal DB-API cursor methods used for connection setup."""

    def execute(self, statement: str) -> object: ...

    def close(self) -> None: ...


class SQLiteConnection(Protocol):
    """Minimal DB-API connection methods used by SQLAlchemy's connect event."""

    def cursor(self) -> SQLiteCursor: ...


def create_engine(settings: Settings) -> AsyncEngine:
    """Create an async engine and ensure SQLite's data directory exists."""
    if settings.database_url.startswith("sqlite+aiosqlite:///./"):
        Path("data").mkdir(parents=True, exist_ok=True)
    engine = create_async_engine(settings.database_url)

    if settings.database_url.startswith("sqlite+"):

        @event.listens_for(engine.sync_engine, "connect")
        def configure_sqlite(connection: SQLiteConnection, _: object) -> None:
            cursor = connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA busy_timeout=5000")
            cursor.close()

    return engine


def create_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    """Create a session factory bound to the application engine."""
    return async_sessionmaker(engine, expire_on_commit=False)
