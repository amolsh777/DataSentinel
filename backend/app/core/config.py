"""Environment-driven application settings.

All settings can be overridden with environment variables prefixed ``DS_``
or via a ``.env`` file in the project root (see ``.env.example``).
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# backend/app/core/config.py -> parents[3] is the repository root.
PROJECT_ROOT = Path(__file__).resolve().parents[3]

_SQLITE_PREFIX = "sqlite:///"


class Settings(BaseSettings):
    """Runtime configuration for the backend."""

    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_prefix="DS_",
        extra="ignore",
    )

    app_name: str = "DataSentinel"
    app_env: str = "development"
    log_level: str = "INFO"

    database_url: str = f"{_SQLITE_PREFIX}data/datasentinel.db"
    storage_dir: Path = Path("data/storage")

    max_upload_mb: float = 50
    max_rows: int = 1_000_000
    max_columns: int = 500
    profile_sample_rows: int = 200_000
    max_preview_rows: int = 100

    cors_origins: str = "http://localhost:8501,http://127.0.0.1:8501"

    # SMTP for email alerts (all optional)
    smtp_host: str | None = None
    smtp_port: int = 587
    smtp_user: str | None = None
    smtp_password: str | None = None
    smtp_from: str | None = None

    # Rate limiting (enforced by app.core.security.RateLimitMiddleware; per process)
    rate_limit_upload: str = "10/minute"
    rate_limit_analysis: str = "30/minute"
    trust_proxy_headers: bool = False  # honour X-Forwarded-For only behind a proxy you control

    # Authentication. Unset = open API (fine for local development; refused in production).
    api_key: str | None = None
    readonly_api_key: str | None = None

    # Outbound webhooks may not target private/internal addresses unless explicitly allowed.
    allow_private_webhooks: bool = False

    # Jobs that have been "running" longer than this at startup are marked failed (interrupted).
    job_stale_minutes: int = 60

    @property
    def is_production(self) -> bool:
        return self.app_env == "production"

    def check_production_safety(self) -> list[str]:
        """Blocking problems: the process refuses to start in production while any remain."""
        issues: list[str] = []
        if not self.is_production:
            return issues
        if not self.api_key:
            issues.append("DS_API_KEY is not set - the API would be completely unauthenticated. "
                          "Generate one with: python -c \"import secrets; print(secrets.token_urlsafe(32))\"")
        elif len(self.api_key) < 16:
            issues.append("DS_API_KEY is shorter than 16 characters.")
        if "localhost" in self.cors_origins or "127.0.0.1" in self.cors_origins:
            issues.append("DS_CORS_ORIGINS contains localhost - set it to your actual frontend origin in production.")
        if self.allow_private_webhooks:
            issues.append("DS_ALLOW_PRIVATE_WEBHOOKS=true allows webhooks to internal addresses (SSRF risk).")
        return issues

    def production_warnings(self) -> list[str]:
        """Non-blocking advice, logged at startup."""
        out: list[str] = []
        if self.is_production and self.is_sqlite:
            out.append("SQLite in production: fine for a single process, use PostgreSQL for concurrent writers.")
        if self.is_production and self.smtp_host is None:
            out.append("DS_SMTP_HOST is not set - email alerts are unavailable (webhooks still work).")
        return out

    @field_validator("database_url")
    @classmethod
    def _resolve_sqlite_path(cls, value: str) -> str:
        """Resolve relative SQLite paths against the project root, not the CWD."""
        if not value.startswith(_SQLITE_PREFIX):
            return value
        path_part = value[len(_SQLITE_PREFIX):]
        if path_part in ("", ":memory:"):
            return value
        path = Path(path_part)
        if not path.is_absolute():
            path = PROJECT_ROOT / path
        return _SQLITE_PREFIX + path.as_posix()

    @field_validator("storage_dir")
    @classmethod
    def _resolve_storage_dir(cls, value: Path) -> Path:
        return value if value.is_absolute() else (PROJECT_ROOT / value)

    @property
    def max_upload_bytes(self) -> int:
        return int(self.max_upload_mb * 1024 * 1024)

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def is_sqlite(self) -> bool:
        return self.database_url.startswith("sqlite")


@lru_cache
def get_settings() -> Settings:
    """Return cached settings loaded from the environment."""
    return Settings()
