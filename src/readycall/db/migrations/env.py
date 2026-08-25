"""Alembic environment.

Two things differ from the generated template, both deliberate:

* **The URL comes from `Settings`**, not from `alembic.ini`. Everything else in this system
  is configured that way (`D3`), and a second place to write a database URL is a second
  place for it to be wrong — including on hackathon morning against the real database.
* **`include_object` refuses to touch the `core` schema.** The bank's data is read-only to
  us (`D5`) and that is already enforced by grants; this is the second lock, so an
  autogenerate run that notices core tables cannot propose dropping them. A migration is
  exactly the kind of thing that runs as a superuser and defeats a grant.
"""

from __future__ import annotations

import asyncio
from logging.config import fileConfig
from typing import Any

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

# Registers every table on Base.metadata. A model in a file nobody imports is a migration
# that never gets written.
import readycall.db.models  # noqa: F401  (side-effect import, intentional)
from readycall.config import get_settings
from readycall.db.base import SCHEMA, Base

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _database_url() -> str:
    return get_settings().database_url


def include_object(
    obj: Any, name: str | None, type_: str, reflected: bool, compare_to: Any
) -> bool:
    """Never emit DDL against the bank's schema (`D5`)."""
    # Alembic's own bookkeeping table is not part of the model, and autogenerate will
    # cheerfully propose dropping it if it happens to exist when the comparison runs.
    if type_ == "table" and name == "alembic_version":
        return False
    schema = getattr(obj, "schema", None)
    return not (type_ == "table" and schema not in (None, SCHEMA))


def run_migrations_offline() -> None:
    context.configure(
        url=_database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        include_schemas=True,
        include_object=include_object,
        version_table_schema=SCHEMA,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        include_schemas=True,
        include_object=include_object,
        # The version table lives in OUR schema. Left in `public` it would be the one
        # piece of our state sitting outside the boundary D5 draws.
        version_table_schema=SCHEMA,
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    section = config.get_section(config.config_ini_section, {})
    section["sqlalchemy.url"] = _database_url()
    connectable = async_engine_from_config(section, prefix="sqlalchemy.", poolclass=pool.NullPool)
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
