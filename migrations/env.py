"""Alembic environment for async SQLite migrations."""

import asyncio
import os
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from dotenv import dotenv_values
from sqlalchemy import pool
from sqlalchemy.engine import Connection, make_url
from sqlalchemy.ext.asyncio import async_engine_from_config

from medslot.models import Base

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def database_url() -> str:
    """Read the database URL from the process or local .env file."""
    env_values = dotenv_values(".env")
    return os.environ.get("DATABASE_URL") or str(
        env_values.get("DATABASE_URL", "sqlite+aiosqlite:///./data/medslot.db")
    )


def run_migrations_offline() -> None:
    """Configure migration SQL rendering without opening a database connection."""
    context.configure(
        url=database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_as_batch=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_sync_migrations(connection: Connection) -> None:
    """Run revisions on the sync connection adapted from the async engine."""
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        render_as_batch=True,
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    """Run revisions using SQLAlchemy's async engine."""
    configuration = config.get_section(config.config_ini_section, {})
    url = database_url()
    if url.startswith("sqlite"):
        database_path = make_url(url).database
        if database_path and database_path != ":memory:":
            Path(database_path).parent.mkdir(parents=True, exist_ok=True)
    configuration["sqlalchemy.url"] = url
    configuration["sqlalchemy.poolclass"] = pool.NullPool
    engine = async_engine_from_config(configuration, prefix="sqlalchemy.")
    async with engine.connect() as connection:
        await connection.run_sync(run_sync_migrations)
    await engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
