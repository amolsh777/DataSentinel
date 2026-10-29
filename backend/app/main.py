"""FastAPI application factory."""

from __future__ import annotations

import logging

from fastapi import APIRouter, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app import __version__
from app.api import analysis, datasets, files, health, jobs, monitoring, quality
from app.core.middleware import RequestContextMiddleware
from app.core.security import ApiKeyAuthMiddleware, RateLimitMiddleware
from app.core.config import Settings, get_settings
from app.core.errors import register_error_handlers
from app.core.logging import configure_logging
from app.services import alert_service, job_service
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
            logging.getLogger(__name__).critical("PRODUCTION CONFIG PROBLEM: %s", problem)
        sys.exit(
            "DataSentinel refuses to start in production with unsafe configuration.\n"
            + "\n".join(f"  • {p}" for p in problems)
        )

    for warning in settings.production_warnings():
        logging.getLogger(__name__).warning("Config: %s", warning)
    if not settings.api_key and not settings.readonly_api_key:
        logging.getLogger(__name__).warning("Config: DS_API_KEY is not set - the API is open (development mode).")

    app = FastAPI(
        lifespan=lifespan,
        title="DataSentinel API",
        version=__version__,
        description="Data quality, drift detection and pipeline intelligence.",
    )
    app.state.settings = settings
    alert_service.configure(settings)
    job_service.recover_interrupted(build_session_factory(engine), settings.job_stale_minutes)
    app.state.engine = engine
    app.state.session_factory = build_session_factory(engine)

    # Order matters (last added = outermost): rate limit -> auth -> CORS -> request context.
    # CORS must wrap auth so pre-flight and 401/429 responses carry CORS headers.
    app.add_middleware(RateLimitMiddleware, upload=settings.rate_limit_upload, analysis=settings.rate_limit_analysis,
                       trust_proxy=settings.trust_proxy_headers)
    app.add_middleware(ApiKeyAuthMiddleware, api_key=settings.api_key, readonly_key=settings.readonly_api_key)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=False,
        allow_methods=["GET", "POST", "PATCH", "DELETE"],
        allow_headers=["*"],
    )
    register_error_handlers(app)

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
