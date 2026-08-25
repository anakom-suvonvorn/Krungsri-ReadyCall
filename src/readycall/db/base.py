"""The declarative base, and the two conventions every table here follows.

**Everything we write lives in the `readycall` schema** (`D5`). The bank's `core` schema is
reachable only through `CoreDataProvider`, with a `SELECT`-only grant behind it, so our
inability to corrupt their data is enforced by the database rather than by everyone
remembering. No model in this package may name a `core` table.

**Constraint names are generated, not improvised.** Alembic's autogenerate cannot drop an
unnamed constraint on SQLite and produces churn on Postgres, so the naming convention below
is set once here. It is the kind of thing that costs five minutes now and a broken migration
later.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, DateTime, MetaData
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.types import TypeDecorator

SCHEMA = "readycall"

NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(schema=SCHEMA, naming_convention=NAMING_CONVENTION)


class Json(TypeDecorator[Any]):
    """`JSONB` on Postgres, plain `JSON` everywhere else.

    Tests run on SQLite so the suite needs no container (`D75`), and SQLite has no JSONB.
    Without this the models would only load under Postgres, which would make the fast test
    path impossible and quietly push verification to "later" — the shape that produced
    `B7`.
    """

    impl = JSON
    cache_ok = True

    def load_dialect_impl(self, dialect: Any) -> Any:
        if dialect.name == "postgresql":
            return dialect.type_descriptor(JSONB())
        return dialect.type_descriptor(JSON())


class Utc(TypeDecorator[datetime]):
    """A timestamp that is always timezone-aware, on every backend.

    SQLite hands back naive datetimes, and a naive value compared against an aware one
    raises — mid-call, in the matcher, where a `TypeError` costs a caller. Everything in
    this system is UTC by construction (`D35`), so the type asserts that rather than hoping.
    """

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_result_value(self, value: datetime | None, dialect: Any) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            from datetime import UTC

            return value.replace(tzinfo=UTC)
        return value


__all__ = ["NAMING_CONVENTION", "SCHEMA", "Base", "Json", "Utc"]
