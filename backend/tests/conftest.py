"""Shared fixtures. Tests never read the real clock or the real environment.

Database fixtures: by default each test gets a fresh SQLite database built by running the real
Alembic migration. Set TEST_DATABASE_URL to a PostgreSQL URL to run the same tests there instead.
They run inside a throw-away schema, so a development database in the same server is never touched.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

import mlops.deployment.infrastructure.orm
import mlops.governance.infrastructure.orm
import mlops.monitoring.infrastructure.orm
import mlops.registry.infrastructure.orm  # noqa: F401  (registers every table on Base.metadata)
from mlops.platform.db import Base, make_engine, make_session_factory
from support.database import migrate

FIXED_NOW = datetime(2026, 1, 15, 12, 0, 0, tzinfo=UTC)


@pytest.fixture
def now() -> datetime:
    """A fixed, timezone-aware instant so time-dependent logic is deterministic."""
    return FIXED_NOW


@pytest.fixture(scope="session")
def postgres_engine() -> Iterator[Engine | None]:
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        yield None
        return
    schema = f"test_{uuid.uuid4().hex[:12]}"
    admin = make_engine(url)
    with admin.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    engine = make_engine(url, connect_args={"options": f"-csearch_path={schema}"})
    migrate(engine)
    yield engine
    engine.dispose()
    with admin.begin() as connection:
        connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
    admin.dispose()


@pytest.fixture
def engine(tmp_path: Path, postgres_engine: Engine | None) -> Iterator[Engine]:
    if postgres_engine is not None:
        tables = ", ".join(f'"{table.name}"' for table in Base.metadata.sorted_tables)
        with postgres_engine.begin() as connection:
            # TRUNCATE does not fire the audit row triggers, so it can reset the append-only table.
            connection.execute(text(f"TRUNCATE TABLE {tables} RESTART IDENTITY CASCADE"))
        yield postgres_engine
        return
    sqlite = make_engine(f"sqlite+pysqlite:///{tmp_path / 'test.db'}")
    migrate(sqlite)
    yield sqlite
    sqlite.dispose()


@pytest.fixture
def session_factory(engine: Engine) -> sessionmaker[Session]:
    return make_session_factory(engine)


@pytest.fixture
def session(session_factory: sessionmaker[Session]) -> Iterator[Session]:
    active = session_factory()
    yield active
    active.rollback()
    active.close()
