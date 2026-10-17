"""Engine/session construction and schema initialisation."""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from app.database.base import Base


def _enable_sqlite_foreign_keys(dbapi_connection, _record) -> None:  # pragma: no cover - driver hook
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


def build_engine(database_url: str) -> Engine:
    """Create an engine. SQLite gets FK enforcement and its directory created."""
    kwargs: dict = {"pool_pre_ping": True}
    is_sqlite = database_url.startswith("sqlite")
    if is_sqlite:
        kwargs["connect_args"] = {"check_same_thread": False}
        path_part = database_url.split("sqlite:///", 1)[-1]
        if path_part and path_part != ":memory:":
            Path(path_part).parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(database_url, **kwargs)
    if is_sqlite:
        event.listen(engine, "connect", _enable_sqlite_foreign_keys)
    return engine


def build_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def init_db(engine: Engine) -> None:
    """Create all tables if they do not exist.

    Hackathon strategy: ``create_all`` + ``scripts/reset_demo.py``. Alembic
    migrations are deferred (see docs/ARCHITECTURE.md).
    """
    import app.models  # noqa: F401  (registers all models on Base.metadata)

    Base.metadata.create_all(engine)
