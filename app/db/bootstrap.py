"""Self-healing schema bootstrap.

The production incident this module exists for:

    asyncpg.exceptions.UndefinedTableError: relation "users" does not exist
    → the bot answered every /start with «❌ انجام این کار با مشکل مواجه شد»

The database was reachable and empty: migrations had never run, because they
live in Railway's `preDeployCommand` / the entrypoint's opt-in
`RUN_MIGRATIONS_ON_START` flag. A single-service deploy (or an overridden
container command) therefore starts a bot that cannot serve one request.

`ensure_schema()` makes the process responsible for its own schema:

1. compare the tables the ORM knows about with the tables the database has;
2. if something is missing (or `RUN_MIGRATIONS_ON_START` is on), run
   `alembic upgrade head` **programmatically**, guarded by a PostgreSQL
   advisory lock so concurrent replicas cannot migrate at the same time;
3. verify again, and if tables are *still* missing and `SCHEMA_AUTOCREATE`
   allows it, create them straight from the ORM metadata (last resort: it never
   alters an existing table, it only adds what is absent).

Everything here is idempotent and cheap: an already-migrated database costs one
`SELECT 1`-class query at boot.
"""
from __future__ import annotations

import logging
from pathlib import Path

from sqlalchemy import inspect, text
from sqlalchemy.ext.asyncio import AsyncEngine

from ..config import PACKAGE_ROOT, normalize_database_url, settings
from .models import Base
from .session import get_engine

log = logging.getLogger(__name__)

#: PostgreSQL advisory lock shared with `app.services.backup.LOCK_KEY_MIGRATION`
MIGRATION_LOCK_KEY = 7_341_002

#: tables that must exist before the bot may accept a single update
CRITICAL_TABLES = ("users", "weekly_plans", "plan_days", "activities")


class SchemaError(RuntimeError):
    """The database is reachable but its schema cannot be brought up to date."""


def _alembic_config() -> "object":
    """Alembic Config that will *not* reconfigure this process's logging.

    `migrations/env.py` calls `fileConfig(config.config_file_name)` when an ini
    file is known, and `fileConfig` defaults to `disable_existing_loggers=True`
    — which would silently mute the bot's own loggers mid-run. Clearing
    `config_file_name` skips that call; everything else (script_location, the
    URL) is set explicitly below.
    """
    from alembic.config import Config

    ini = PACKAGE_ROOT / "alembic.ini"
    config = Config(str(ini)) if ini.exists() else Config()
    config.set_main_option("script_location", str(PACKAGE_ROOT / "migrations"))
    config.set_main_option("prepend_sys_path", str(PACKAGE_ROOT))
    dsn = normalize_database_url(settings.database_url)
    # '%' must be escaped: set_main_option interpolates the value
    config.set_main_option("sqlalchemy.url", dsn.replace("%", "%%"))
    config.config_file_name = None
    # older alembic releases also accept the kwarg on command.upgrade()
    try:
        config.attributes["configure_logger"] = False
    except Exception:  # pragma: no cover - very old alembic
        pass
    return config


async def current_revision(engine: AsyncEngine | None = None) -> str | None:
    """The `alembic_version` the database is on (None when uninitialised)."""
    eng = engine or get_engine()
    try:
        async with eng.connect() as conn:
            has_table = await conn.run_sync(
                lambda sync_conn: inspect(sync_conn).has_table("alembic_version")
            )
            if not has_table:
                return None
            row = (
                await conn.execute(text("SELECT version_num FROM alembic_version"))
            ).first()
            return row[0] if row else None
    except Exception as exc:  # pragma: no cover - unreadable catalogue
        log.warning("could not read alembic_version: %s", exc)
        return None


async def head_revision() -> str | None:
    """The newest revision in `migrations/versions` (no database needed)."""
    try:
        from alembic.script import ScriptDirectory

        script = ScriptDirectory(str(PACKAGE_ROOT / "migrations"))
        return script.get_current_head()
    except Exception as exc:  # pragma: no cover - broken revision chain
        log.warning("could not resolve the alembic head revision: %s", exc)
        return None


async def missing_tables(engine: AsyncEngine | None = None) -> list[str]:
    """Tables the ORM defines but the database does not have."""
    eng = engine or get_engine()
    expected = set(Base.metadata.tables)
    try:
        async with eng.connect() as conn:
            present = set(await conn.run_sync(lambda c: inspect(c).get_table_names()))
    except Exception as exc:
        raise SchemaError(f"cannot inspect the database schema: {exc}") from exc
    return sorted(expected - present)


async def run_migrations() -> None:
    """`alembic upgrade head`, executed in-process under an advisory lock.

    The lock lives on a dedicated unpooled connection (see
    `app.services.backup.migration_lock`) so that it can never leak back into
    the application's connection pool.
    """
    from alembic import command

    from ..services.backup import migration_lock

    config = _alembic_config()
    async with migration_lock():
        # alembic is synchronous and creates its own event loop internally
        await _to_thread(command.upgrade, config, "head")
    log.info("alembic upgrade head: done")


async def _to_thread(function, *args, **kwargs):
    """Alembic is synchronous and opens its own event loop — never block ours."""
    import asyncio

    return await asyncio.to_thread(function, *args, **kwargs)


async def create_missing_from_orm(engine: AsyncEngine | None = None) -> list[str]:
    """Last resort: `CREATE TABLE` for whatever is still absent.

    `create_all(checkfirst=True)` never alters or drops an existing table, so
    this cannot damage a migrated database; it only fills the gaps.
    """
    eng = engine or get_engine()
    before = await missing_tables(eng)
    if not before:
        return []
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    after = set(await missing_tables(eng))
    created = [name for name in before if name not in after]
    if created:
        log.warning(
            "created %s table(s) directly from the ORM because migrations did not "
            "run: %s — run `alembic upgrade head` to keep the schema versioned",
            len(created), ", ".join(created),
        )
    return created


async def ensure_schema(engine: AsyncEngine | None = None) -> dict:
    """Bring the schema up to date. Called once at boot, before polling starts."""
    eng = engine or get_engine()
    report = {
        "missing_before": [],
        "migrated": False,
        "created_from_orm": [],
        "revision": None,
        "head": None,
        "ok": False,
    }

    report["missing_before"] = await missing_tables(eng)
    needs_migration = bool(report["missing_before"]) or settings.run_migrations_on_start

    if needs_migration:
        try:
            await run_migrations()
            report["migrated"] = True
        except Exception as exc:
            log.error("alembic upgrade head failed: %s", exc)
            if not report["missing_before"]:
                # nothing is actually missing — a failed no-op upgrade must not
                # stop the bot from serving traffic
                report["ok"] = True
                report["revision"] = await current_revision(eng)
                report["head"] = await head_revision()
                return report
            if not settings.schema_autocreate:
                raise SchemaError(
                    f"migrations failed and SCHEMA_AUTOCREATE is off: {exc}"
                ) from exc

    still_missing = await missing_tables(eng)
    if still_missing:
        if not settings.schema_autocreate:
            raise SchemaError(
                "database schema is incomplete and SCHEMA_AUTOCREATE is off: "
                + ", ".join(still_missing)
            )
        report["created_from_orm"] = await create_missing_from_orm(eng)

    final_missing = await missing_tables(eng)
    critical = [name for name in CRITICAL_TABLES if name in final_missing]
    if critical:
        raise SchemaError(
            "the database is missing required tables after bootstrap: "
            + ", ".join(critical)
            + " — check DATABASE_URL points at the right database and that the "
              "role may create tables"
        )

    report["revision"] = await current_revision(eng)
    report["head"] = await head_revision()
    report["ok"] = True
    if report["migrated"] or report["created_from_orm"]:
        log.info(
            "schema ready · revision=%s head=%s created_from_orm=%s",
            report["revision"], report["head"], report["created_from_orm"] or "—",
        )
    if report["revision"] and report["head"] and report["revision"] != report["head"]:
        log.warning(
            "schema drift: database is on revision %s but the code expects %s — "
            "run `alembic upgrade head`",
            report["revision"], report["head"],
        )
    return report


def migrations_available() -> bool:
    """Cheap preflight: are the alembic files even in the image?"""
    versions = PACKAGE_ROOT / "migrations" / "versions"
    return Path(versions).is_dir() and bool(list(Path(versions).glob("*.py")))


__all__ = [
    "CRITICAL_TABLES",
    "MIGRATION_LOCK_KEY",
    "SchemaError",
    "create_missing_from_orm",
    "current_revision",
    "ensure_schema",
    "head_revision",
    "migrations_available",
    "missing_tables",
    "run_migrations",
]
