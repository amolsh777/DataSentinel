"""FastAPI application factory."""

from __future__ import annotations

from fastapi import APIRouter, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app import __version__
from app.api import analysis, datasets, files, health, jobs, monitoring, quality
from app.core.middleware import RequestContextMiddleware
from app.core.config import Settings, get_settings
from app.core.errors import register_error_handlers
from app.core.logging import configure_logging
from app.database.session import build_engine, build_session_factory, init_db


def create_app(settings: Settings | None = None, lifespan=None) -> FastAPI:
    """Build the API. Tests pass their own ``Settings`` (temp DB + storage)."""
    settings = settings or get_settings()
    configure_logging(settings.log_level)

    engine = build_engine(settings.database_url)
    init_db(engine)
    settings.storage_dir.mkdir(parents=True, exist_ok=True)

    # Production safety check — refuses to start when misconfigured.
    problems = settings.check_production_safety()
    if problems:
        import sys
        for problem in problems:
            import logging
            logging.getLogger(__name__).critical("PRODUCTION CONFIG PROBLEM: %s", problem)
        sys.exit(
            "DataSentinel refuses to start in production with unsafe configuration.\n"
            + "\n".join(f"  • {p}" for p in problems)
        )

    app = FastAPI(
        lifespan=lifespan,
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

    # Rate limiting (10 uploads/min per IP; analysis 30/min).
    try:
        from slowapi import Limiter, _rate_limit_exceeded_handler
        from slowapi.util import get_remote_address
        from slowapi.errors import RateLimitExceeded
        limiter = Limiter(key_func=get_remote_address)
        app.state.limiter = limiter
        app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
    except ImportError:
        pass  # slowapi optional

    app.add_middleware(RequestContextMiddleware)

    api = APIRouter(prefix="/api")
    api.include_router(health.router)
    api.include_router(files.router)
    api.include_router(datasets.router)
    api.include_router(quality.dataset_quality_router)
    api.include_router(quality.rules_router)
    api.include_router(analysis.router)
    api.include_router(monitoring.runs_router)
    api.include_router(monitoring.incidents_router)
    api.include_router(monitoring.reports_router)
    api.include_router(monitoring.alert_router)
    api.include_router(monitoring.scheduler_router)
    api.include_router(monitoring.lineage_router)
    api.include_router(jobs.router)
    app.include_router(api)

    # /api/v1 — same functionality, versioned prefix for future contract changes
    v1 = APIRouter(prefix="/api/v1")
    v1.include_router(health.router)
    v1.include_router(files.router)
    v1.include_router(datasets.router)
    v1.include_router(quality.dataset_quality_router)
    v1.include_router(quality.rules_router)
    v1.include_router(analysis.router)
    v1.include_router(monitoring.runs_router)
    v1.include_router(monitoring.incidents_router)
    v1.include_router(monitoring.reports_router)
    v1.include_router(monitoring.alert_router)
    v1.include_router(monitoring.scheduler_router)
    v1.include_router(monitoring.lineage_router)
    v1.include_router(jobs.router)
    app.include_router(v1)
    return app
