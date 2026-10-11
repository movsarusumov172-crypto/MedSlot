"""SQLite-backed aiogram FSM storage that survives process restarts."""

from typing import Any

from aiogram.fsm.state import State
from aiogram.fsm.storage.base import BaseStorage, StateType, StorageKey
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from medslot.models import FSMStateRecord

FSM_KEY_COLUMNS = (
    FSMStateRecord.bot_id,
    FSMStateRecord.chat_id,
    FSMStateRecord.user_id,
    FSMStateRecord.thread_id,
    FSMStateRecord.business_connection_id,
    FSMStateRecord.destiny,
)


class SqliteFSMStorage(BaseStorage):
    """Persist FSM states and JSON-serializable dialog data in SQLite."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self.session_factory = session_factory

    async def set_state(self, key: StorageKey, state: StateType = None) -> None:
        """Set or clear the state while preserving the dialog's data."""
        state_value = state.state if isinstance(state, State) else state
        values = self._key_values(key)
        values["state"] = state_value
        values["data"] = {}
        statement = insert(FSMStateRecord).values(**values)
        statement = statement.on_conflict_do_update(
            index_elements=FSM_KEY_COLUMNS,
            set_={"state": state_value},
        )
        async with self.session_factory() as session:
            await session.execute(statement)
            await session.commit()

    async def get_state(self, key: StorageKey) -> str | None:
        """Read one persisted dialog state."""
        async with self.session_factory() as session:
            record = await session.get(FSMStateRecord, self._identity(key))
            return record.state if record is not None else None

    async def set_data(self, key: StorageKey, data: dict[str, Any]) -> None:
        """Replace the persisted JSON data while preserving the current state."""
        values = self._key_values(key)
        values["state"] = None
        values["data"] = dict(data)
        statement = insert(FSMStateRecord).values(**values)
        statement = statement.on_conflict_do_update(
            index_elements=FSM_KEY_COLUMNS,
            set_={"data": dict(data)},
        )
        async with self.session_factory() as session:
            await session.execute(statement)
            await session.commit()

    async def get_data(self, key: StorageKey) -> dict[str, Any]:
        """Read the persisted JSON data or return an empty mapping."""
        async with self.session_factory() as session:
            record = await session.get(FSMStateRecord, self._identity(key))
            return dict(record.data) if record is not None and record.data else {}

    async def update_data(self, key: StorageKey, data: dict[str, Any]) -> dict[str, Any]:
        """Merge a partial update into the stored JSON data."""
        current_data = await self.get_data(key)
        current_data.update(data)
        await self.set_data(key, current_data)
        return current_data

    async def close(self) -> None:
        """Leave the shared engine lifecycle to the application."""

    @staticmethod
    def _key_values(key: StorageKey) -> dict[str, object]:
        return {
            "bot_id": key.bot_id,
            "chat_id": key.chat_id,
            "user_id": key.user_id,
            "thread_id": key.thread_id if key.thread_id is not None else -1,
            "business_connection_id": key.business_connection_id or "",
            "destiny": key.destiny,
        }

    @classmethod
    def _identity(cls, key: StorageKey) -> tuple[object, ...]:
        values = cls._key_values(key)
        return tuple(values[column.key] for column in FSM_KEY_COLUMNS)
