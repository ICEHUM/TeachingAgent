from __future__ import annotations

import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from app.business import BUSINESS_SCHEMA
from app.business.models import Base

config = context.config
if config.config_file_name:
    fileConfig(config.config_file_name)
if os.environ.get("TEACHING_ALEMBIC_DATABASE_URL"):
    # Alembic stores options in ConfigParser, where a literal percent must be doubled.
    environment_url = os.environ["TEACHING_ALEMBIC_DATABASE_URL"].replace("%", "%%")
    config.set_main_option("sqlalchemy.url", environment_url)
target_metadata = Base.metadata


def include_name(name, type_, parent_names):
    if type_ == "schema":
        return name in {None, BUSINESS_SCHEMA}
    return True


def configure(connection=None, url=None):
    context.configure(
        connection=connection,
        url=url,
        target_metadata=target_metadata,
        include_schemas=True,
        include_name=include_name,
        version_table="alembic_version",
        version_table_schema=BUSINESS_SCHEMA,
        compare_type=True,
        literal_binds=url is not None,
        dialect_opts={"paramstyle": "named"} if url is not None else None,
    )


def run_migrations_offline():
    configure(url=config.get_main_option("sqlalchemy.url"))
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online():
    connectable = engine_from_config(config.get_section(config.config_ini_section), prefix="sqlalchemy.", poolclass=pool.NullPool)
    with connectable.connect() as connection:
        configure(connection=connection)
        with context.begin_transaction():
            context.run_migrations()


run_migrations_offline() if context.is_offline_mode() else run_migrations_online()
