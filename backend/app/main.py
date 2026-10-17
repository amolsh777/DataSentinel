"""FastAPI application factory."""

from __future__ import annotations

from fastapi import APIRouter, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app import __version__
from app.api import analysis, datasets, files, health, quality
from app.core.config import Settings, get_settings
from app.core.errors import register_error_handlers
from app.core.logging import configure_logging
from app.database.session import build_engine, build_session_factory, init_db


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build the API. Tests pass their own ``Settings`` (temp DB + storage)."""
    settings = settings or get_settings()
    configure_logging(settings.log_level)

    engine = build_engine(settings.database_url)
    init_db(engine)
    settings.storage_dir.mkdir(parents=True, exist_ok=True)

    app = FastAPI(
        title="DataSentinel API",
        version=__version__,
        description="Data quality, drift detection and pipeline intelligence.",
    )
    app.state.settings = settings
    app.state.engine = engine
    app.state.session_factory = build_session_factory(engine)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=False,
        allow_methods=["GET", "POST", "PATCH", "DELETE"],
        allow_headers=["*"],
    )
    register_error_handlers(app)

    api = APIRouter(prefix="/api")
    api.include_router(health.router)
    api.include_router(files.router)
    api.include_router(datasets.router)
    api.include_router(quality.dataset_quality_router)
    api.include_router(quality.rules_router)
    api.include_router(analysis.router)
    app.include_router(api)
    return app
