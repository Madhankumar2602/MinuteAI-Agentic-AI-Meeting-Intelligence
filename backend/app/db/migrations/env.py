"""Alembic environment.

Two deviations from the generated template:

1. The database URL comes from ``app.core.config.settings`` rather than
   ``alembic.ini``. One source of truth, and no credentials in a committed file.
2. ``ALEMBIC_DATABASE_URL`` overrides it when set, which is how the pytest
   fixtures point migrations at the isolated test database.
"""

from __future__ import annotations

import asyncio
import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

# Importing the models package registers every model on Base.metadata, which is
# what `alembic revision --autogenerate` compares against the live database.
import app.db.models  # noqa: F401
from app.core.config import settings
from app.db.base import Base

config = context.config

if config.config_file_name is not None:
    # disable_existing_loggers=False: the default (True) switches off every
    # logger created before migrations run. Running migrations in-process (the
    # test suite does) would otherwise silently disable all application logging.
    fileConfig(config.config_file_name, disable_existing_loggers=False)

config.set_main_option(
    "sqlalchemy.url",
    os.getenv("ALEMBIC_DATABASE_URL", settings.database_url),
)

target_metadata = Base.metadata


def _configure(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        # Detect column type changes (e.g. String(255) -> String(320)).
        compare_type=True,
        # Detect added/removed server defaults.
        compare_server_default=True,
    )


def run_migrations_offline() -> None:
    """Emit SQL to stdout without connecting - useful for reviewing a migration."""
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    _configure(connection)
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
