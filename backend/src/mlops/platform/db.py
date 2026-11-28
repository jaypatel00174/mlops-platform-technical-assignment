"""Database plumbing shared by every module: declarative base, portable column types, engine and
session factories, a unit-of-work helper and integrity-error classification.

Everything here is portable across PostgreSQL (production) and SQLite (fast local tests).
PostgreSQL-only optimisations, such as time partitioning, live in optional migrations.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Any, Literal

from sqlalchemy import JSON, DateTime, MetaData, create_engine, event
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.engine import Dialect, Engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker
from sqlalchemy.types import TypeDecorator

# Predictable constraint and index names make migrations reviewable and errors greppable.
NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_N_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


class UTCDateTime(TypeDecorator[datetime]):
    """Timezone-aware UTC datetimes on every database.

    SQLite drops time zones, so values come back naive. This type refuses naive datetimes on the
    way in and always returns aware UTC datetimes on the way out.
    """

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError("naive datetimes are not allowed; use timezone-aware values")
        return value.astimezone(UTC)

    def process_result_value(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)


# JSONB on PostgreSQL (indexable, compact), plain JSON elsewhere.
JsonDocument = JSON().with_variant(JSONB(), "postgresql")


def make_engine(url: str, **kwargs: Any) -> Engine:
    engine = create_engine(url, pool_pre_ping=True, **kwargs)
    if engine.dialect.name == "sqlite":

        @event.listens_for(engine, "connect")
        def _enforce_foreign_keys(dbapi_connection: Any, _record: Any) -> None:
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

    return engine


def make_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(engine, expire_on_commit=False)


@contextmanager
def unit_of_work(factory: sessionmaker[Session]) -> Iterator[Session]:
    """One transaction: commit if the block succeeds, roll back if it raises."""
    session = factory()
    try:
        yield session
        session.commit()
    except BaseException:
        session.rollback()
        raise
    finally:
        session.close()


IntegrityKind = Literal["unique", "foreign_key", "other"]
Translator = Callable[[IntegrityKind, IntegrityError], Exception | None]


def flush_translating(session: Session, translate: Translator) -> None:
    """Flush pending changes and turn a database integrity failure into a typed domain error.

    An integrity failure aborts the transaction, so the session is rolled back first and stays
    usable (the translator may query, for example to find the conflicting row). Anything the
    translator does not recognise is re-raised unchanged.
    """
    try:
        session.flush()
    except IntegrityError as error:
        session.rollback()
        translated = translate(integrity_kind(error), error)
        if translated is not None:
            raise translated from error
        raise


def integrity_kind(error: IntegrityError) -> IntegrityKind:
    """Which kind of constraint failed, for PostgreSQL (SQLSTATE) and SQLite (message)."""
    sqlstate = getattr(error.orig, "sqlstate", None)
    if sqlstate == "23505":
        return "unique"
    if sqlstate == "23503":
        return "foreign_key"
    text = str(error.orig).upper()
    if "UNIQUE CONSTRAINT FAILED" in text:
        return "unique"
    if "FOREIGN KEY CONSTRAINT FAILED" in text:
        return "foreign_key"
    return "other"
