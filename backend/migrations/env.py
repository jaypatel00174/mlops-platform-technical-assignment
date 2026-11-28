"""Alembic environment.

The database URL comes from the application settings (DATABASE_URL). Tests hand in an open
connection through `config.attributes["connection"]` so migrations run on the test database.
"""

from __future__ import annotations

from logging.config import fileConfig

from alembic import context
from sqlalchemy import Connection, create_engine, pool

# Importing the ORM modules registers every table on Base.metadata.
import mlops.deployment.infrastructure.orm
import mlops.governance.infrastructure.orm
import mlops.monitoring.infrastructure.orm
import mlops.registry.infrastructure.orm  # noqa: F401
from mlops.platform.config import get_settings
from mlops.platform.db import Base

config = context.config
if config.config_file_name is not None and config.attributes.get("configure_logger", True):
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _configure(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        render_as_batch=connection.dialect.name == "sqlite",
    )


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url") or get_settings().database_url
    context.configure(url=url, target_metadata=target_metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connection = config.attributes.get("connection")
    if connection is not None:
        _configure(connection)
        with context.begin_transaction():
            context.run_migrations()
        return

    url = config.get_main_option("sqlalchemy.url") or get_settings().database_url
    engine = create_engine(url, poolclass=pool.NullPool)
    with engine.connect() as new_connection:
        _configure(new_connection)
        with context.begin_transaction():
            context.run_migrations()
        new_connection.commit()
    engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
