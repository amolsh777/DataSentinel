"""Alembic environment. Reads DS_DATABASE_URL from the environment (or .env)."""

from __future__ import annotations

import os
import sys
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy import engine_from_config, pool

# Ensure the backend package is importable.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.config import Settings
from app.database.base import Base
import app.models  # noqa: F401 — registers all ORM models on Base.metadata

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata

# Override the sqlalchemy.url from the Settings object so the same DS_DATABASE_URL
# env-var that controls the app also controls migrations.
def _url() -> str:
    s = Settings()
    return s.database_url

def run_migrations_offline() -> None:
    url = _url()
    context.configure(url=url, target_metadata=target_metadata, literal_binds=True,
                      dialect_opts={"paramstyle": "named"})
    with context.begin_transaction():
        context.run_migrations()

def run_migrations_online() -> None:
    cfg = config.get_section(config.config_ini_section, {})
    cfg["sqlalchemy.url"] = _url()
    connectable = engine_from_config(cfg, prefix="sqlalchemy.", poolclass=pool.NullPool)
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata,
                          # SQLite needs this to use batched ALTER TABLE emulation
                          render_as_batch=True)
        with context.begin_transaction():
            context.run_migrations()

if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
