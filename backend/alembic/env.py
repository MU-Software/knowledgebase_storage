from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from alembic import context
from sqlmodel import SQLModel

import backend.models  # noqa: F401
from backend.settings import get_settings

if TYPE_CHECKING:
    from sqlalchemy.engine import Connection

config = context.config
target_metadata = SQLModel.metadata
config.set_main_option("sqlalchemy.url", get_settings().database.sqlalchemy_url)


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata, compare_type=True)
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    engine = get_settings().async_engine
    async with engine.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
