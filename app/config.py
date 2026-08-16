"""Central configuration (environment driven). No secrets ever live in code."""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parents[1]


def _bool(name: str, default: bool = False) -> bool:
    return os.getenv(name, str(default)).strip().lower() in {"1", "true", "yes", "on"}


def _int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, default))
    except (TypeError, ValueError):
        return default


def _float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, default))
    except (TypeError, ValueError):
        return default


def normalize_database_url(url: str) -> str:
    """Railway/Heroku hand out sync URLs — force the async driver.

    postgres://…  /  postgresql://…  →  postgresql+asyncpg://…
    Also strips libpq-only query args that asyncpg rejects (e.g. sslmode).
    """
    if not url:
        return url
    url = re.sub(r"^postgres(ql)?://", "postgresql+asyncpg://", url)
    if url.startswith("postgresql+asyncpg://") and "sslmode=" in url:
        url = re.sub(r"[?&]sslmode=[^&]*", "", url).rstrip("?&")
    return url


def _token() -> str:
    """BOT_TOKEN is canonical; TELEGRAM_BOT_TOKEN kept as a legacy alias."""
    return (os.getenv("BOT_TOKEN") or os.getenv("TELEGRAM_BOT_TOKEN") or "").strip()


@dataclass
class Settings:
    bot_token: str = field(default_factory=_token)
    database_url: str = field(
        default_factory=lambda: normalize_database_url(
            os.getenv("DATABASE_URL", "postgresql+asyncpg://rotbeland:rotbeland@localhost/rotbeland")
        )
    )
    environment: str = field(default_factory=lambda: os.getenv("ENVIRONMENT", "production"))
    redis_url: str | None = field(default_factory=lambda: (os.getenv("REDIS_URL") or "").strip() or None)
    render_backend: str = field(default_factory=lambda: os.getenv("RENDER_BACKEND", "auto"))
    storage_root: Path = field(
        default_factory=lambda: Path(os.getenv("STORAGE_ROOT", str(PACKAGE_ROOT / "generated")))
    )
    template: str = field(default_factory=lambda: os.getenv("TEMPLATE", "template_weekly_v1"))
    timezone: str = field(default_factory=lambda: os.getenv("TIMEZONE", "Asia/Tehran"))
    retention_days: int = field(default_factory=lambda: _int("RETENTION_DAYS", 0))
    pdf_dpi: int = field(default_factory=lambda: _int("PDF_DPI", 300))
    print_scale: float = field(default_factory=lambda: _float("PRINT_SCALE", 2.0))
    render_concurrency: int = field(default_factory=lambda: _int("RENDER_CONCURRENCY", 2))
    admin_ids: tuple[int, ...] = field(
        default_factory=lambda: tuple(
            int(x) for x in os.getenv("ADMIN_IDS", "").replace(" ", "").split(",")
            if x.strip().lstrip("-").isdigit()
        )
    )
    # database pool
    db_pool_size: int = field(default_factory=lambda: _int("DB_POOL_SIZE", 5))
    db_max_overflow: int = field(default_factory=lambda: _int("DB_MAX_OVERFLOW", 5))
    db_pool_recycle: int = field(default_factory=lambda: _int("DB_POOL_RECYCLE", 1800))
    db_connect_retries: int = field(default_factory=lambda: _int("DB_CONNECT_RETRIES", 10))
    sql_echo: bool = field(default_factory=lambda: _bool("SQL_ECHO"))
    # ui / logging
    log_level: str = field(default_factory=lambda: os.getenv("LOG_LEVEL", "INFO").upper())
    students_page_size: int = field(default_factory=lambda: _int("STUDENTS_PAGE_SIZE", 8))
    plans_page_size: int = field(default_factory=lambda: _int("PLANS_PAGE_SIZE", 6))
    health_port: int | None = field(
        default_factory=lambda: _int("PORT", 0) or None
    )

    @property
    def is_sqlite(self) -> bool:
        return self.database_url.startswith("sqlite")

    @property
    def is_production(self) -> bool:
        return self.environment.lower() == "production"

    def validate_for_runtime(self) -> list[str]:
        """Fail fast with actionable messages instead of a stack trace at startup."""
        problems: list[str] = []
        if not self.bot_token:
            problems.append("BOT_TOKEN is not set (get one from @BotFather).")
        elif ":" not in self.bot_token:
            problems.append("BOT_TOKEN looks malformed (expected '<id>:<secret>').")
        if not self.database_url:
            problems.append("DATABASE_URL is not set.")
        if self.is_production and self.is_sqlite:
            problems.append(
                "SQLite is not supported in production — point DATABASE_URL at PostgreSQL."
            )
        return problems

    def safe_summary(self) -> dict[str, str]:
        """Loggable configuration: secrets are masked, never printed raw."""
        return {
            "environment": self.environment,
            "database": _mask_dsn(self.database_url),
            "redis": "configured" if self.redis_url else "memory",
            "render_backend": self.render_backend,
            "storage_root": str(self.storage_root),
            "timezone": self.timezone,
            "bot_token": mask_token(self.bot_token),
            "admins": str(len(self.admin_ids)),
        }


def mask_token(token: str) -> str:
    if not token:
        return "<unset>"
    head, _, _ = token.partition(":")
    return f"{head}:***"


def _mask_dsn(dsn: str) -> str:
    return re.sub(r"//([^:/@]+):([^@]+)@", r"//\1:***@", dsn)


settings = Settings()
