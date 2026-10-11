"""Run Alembic upgrades against the configured database before bot startup."""

import os
from pathlib import Path

from alembic import command
from alembic.config import Config


def upgrade_database(database_url: str) -> None:
    """Apply all pending migrations using the repository's Alembic config."""
    project_root = Path(__file__).resolve().parents[2]
    os.environ["DATABASE_URL"] = database_url
    config = Config(str(project_root / "alembic.ini"))
    command.upgrade(config, "head")
