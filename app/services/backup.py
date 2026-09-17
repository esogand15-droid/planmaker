"""Backups: dump → package → deliver → schedule.

The bot owns its data, so it must own its backups. One archive contains
everything needed to bring the system back:

    rotbeland-backup-1405-06-26_0315.tar.gz
    ├── README.txt              how to restore (Persian + English)
    ├── restore.sh              psql one-liner wrapper
    ├── metadata.json           version, counts, sha256 of every member
    ├── checksums.txt           sha256 per member
    ├── sql/dump.sql            full, restorable SQL (schema + data + sequences)
    ├── sql/schema.sql          schema only
    ├── json/<table>.json       every table as JSON (readable, diffable)
    └── csv/<table>.csv         every table as CSV (opens in Excel/Sheets)

Two engines produce `dump.sql`:

* **pg_dump** — used when the binary is on PATH (the Docker image installs
  `postgresql-client`). Byte-for-byte a normal PostgreSQL dump.
* **python** — pure SQLAlchemy/asyncpg: reflects the *live* schema, emits
  `CREATE TABLE IF NOT EXISTS`, batched `INSERT`s and `setval()` sequence
  resets. No extra dependency, works on any DATABASE_URL the app supports.

`BackupScheduler` implements «بکاپ خودکار»: it reads the schedule the admin
picked in the panel (`bot_settings`), takes the archive when it is due and
sends it to every admin chat. A PostgreSQL advisory lock guarantees that a
second replica can never produce a duplicate archive.
"""
from __future__ import annotations

import asyncio
import csv
import hashlib
import io
import json
import logging
import os
import shutil
import tarfile
import tempfile
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from urllib.parse import unquote, urlparse

from aiogram import Bot
from aiogram.types import FSInputFile
from sqlalchemy import MetaData, inspect, text
from sqlalchemy.ext.asyncio import AsyncConnection
from sqlalchemy.schema import CreateIndex, CreateTable

from ..config import settings
from ..db.models import BackupLog, BackupSchedule, BackupStatus, BotSettings
from ..repositories.repositories import AuditRepository, BackupRepository, SettingsRepository

log = logging.getLogger(__name__)

APP_NAME = "rotbeland"
SQL_DIRNAME = "sql"

#: runtime bookkeeping of the backup feature itself — never part of a dump.
#: (`bot_settings` holds the schedule, which lives in the environment too, and
#: `backup_logs` would otherwise contain the row of the backup being taken.)
EXCLUDED_TABLES = frozenset({"backup_logs", "bot_settings"})

#: PostgreSQL advisory-lock keys (arbitrary, stable, namespaced by hash)
LOCK_KEY_BACKUP = 7_341_001
LOCK_KEY_MIGRATION = 7_341_002

INSERT_BATCH_ROWS = 100
_INSERT_BATCH_BYTES = 180_000

_PG_DUMP_TIMEOUT = int(os.getenv("PG_DUMP_TIMEOUT", "600"))


class BackupError(Exception):
    """User-facing backup failure; the detail is safe to show an admin."""


@dataclass
class BackupArtifact:
    """Result of one successful backup."""

    path: Path
    filename: str
    size_bytes: int
    engine: str
    tables: int
    rows: int
    sha256: str
    duration_ms: int
    row_counts: dict[str, int] = field(default_factory=dict)
    members: dict[str, str] = field(default_factory=dict)
    warning: str | None = None

    @property
    def human_size(self) -> str:
        return human_bytes(self.size_bytes)


def human_bytes(size: float | int) -> str:
    value = float(size or 0)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{value:.1f} {unit}" if unit != "B" else f"{int(value)} B"
        value /= 1024
    return f"{value:.1f} GB"


# ══════════════════════════════════════════════════════════════ helpers ════
def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _libpq_url(url: str | None = None) -> str:
    """`postgresql+asyncpg://…` → `postgresql://…` for command-line tools."""
    from ..config import normalize_database_url

    dsn = normalize_database_url(url or settings.database_url)
    dsn = dsn.replace("postgresql+asyncpg://", "postgresql://", 1)
    dsn = dsn.replace("postgres+asyncpg://", "postgresql://", 1)
    return dsn.replace("postgresql+psycopg://", "postgresql://", 1)


def _pg_dump_binary() -> str | None:
    for name in ("pg_dump", "pg_dump16", "pg_dump15", "pg_dump14"):
        found = shutil.which(name)
        if found:
            return found
    return None


def sql_literal(value) -> str:
    """Render one Python value as a portable SQL literal.

    Values come straight from the driver (`SELECT *` on a reflected table), so
    only native types appear: str/int/float/bool/Decimal/datetime/date/bytes.
    """
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, (int, Decimal)):
        return str(value)
    if isinstance(value, float):
        if value != value or value in (float("inf"), float("-inf")):  # NaN/inf
            return f"'{value}'"
        return repr(value)
    if isinstance(value, datetime):
        return "'" + value.isoformat(sep=" ").replace("+00:00", "+00") + "'"
    if isinstance(value, (bytes, bytearray, memoryview)):
        return "'\\x" + bytes(value).hex() + "'"
    string = value if isinstance(value, str) else str(value)
    if any(ch in string for ch in ("\\", "\n", "\r", "\t")):
        escaped = (
            string.replace("\\", "\\\\")
            .replace("'", "''")
            .replace("\n", "\\n")
            .replace("\r", "\\r")
            .replace("\t", "\\t")
        )
        return "E'" + escaped + "'"
    return "'" + string.replace("'", "''") + "'"


def json_default(value):
    if isinstance(value, (datetime,)):
        return value.isoformat()
    if hasattr(value, "isoformat"):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (bytes, bytearray, memoryview)):
        return bytes(value).hex()
    if hasattr(value, "value") and isinstance(getattr(value, "value"), str):
        return value.value  # enums
    return str(value)


def csv_text(value) -> str:
    if value is None:
        return ""
    if isinstance(value, (bytes, bytearray, memoryview)):
        return bytes(value).hex()
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# ══════════════════════════════════════════════════════════ engine: pg_dump ═
async def pg_dump_archive(out_dir: Path) -> tuple[Path, str]:
    """Full custom-format dump via the PostgreSQL client (best fidelity)."""
    binary = _pg_dump_binary()
    if binary is None:
        raise BackupError("pg_dump is not installed")
    dsn = _libpq_url()
    parsed = urlparse(dsn)
    env = dict(os.environ)
    if parsed.password:
        env["PGPASSWORD"] = unquote(parsed.password)
    env.setdefault("PGCONNECT_TIMEOUT", "20")

    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / "dump.pgdump"
    cmd = [
        binary,
        "--format=custom",
        "--compress=6",
        "--no-owner",
        "--no-privileges",
        "--verbose",
        f"--file={target}",
        dsn,
    ]
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        env=env,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        _out, err = await asyncio.wait_for(proc.communicate(), timeout=_PG_DUMP_TIMEOUT)
    except asyncio.TimeoutError:  # pragma: no cover - pathological databases
        proc.kill()
        raise BackupError(f"pg_dump timed out after {_PG_DUMP_TIMEOUT}s") from None
    if proc.returncode != 0:
        detail = (err or b"").decode("utf-8", "replace").strip().splitlines()
        raise BackupError(f"pg_dump failed: {detail[-1] if detail else proc.returncode}")
    if not target.exists() or target.stat().st_size == 0:
        raise BackupError("pg_dump produced an empty file")
    return target, "pg_dump"


# ══════════════════════════════════════════════════════════ engine: python ═
def _compile_ddl(element, dialect) -> str:
    compiled = element.compile(dialect=dialect)
    return str(compiled).strip().rstrip(";") + ";"


def _sequence_name(default: str | None) -> str | None:
    """`nextval('users_id_seq'::regclass)` → `users_id_seq` (quotes removed)."""
    if not default or "nextval(" not in default:
        return None
    inside = default.split("nextval(", 1)[1].split(")", 1)[0]
    inside = inside.split("::", 1)[0].strip()
    if len(inside) >= 2 and inside[0] == "'" and inside[-1] == "'":
        inside = inside[1:-1]
    return inside or None


async def _sequences_of(conn: AsyncConnection, table: str, columns: list[str]) -> dict[str, str]:
    """column → sequence name, read from the live column defaults (PostgreSQL)."""
    if conn.dialect.name != "postgresql":
        return {}
    try:
        rows = (
            await conn.execute(
                text(
                    "SELECT column_name, column_default FROM information_schema.columns "
                    "WHERE table_schema = 'public' AND table_name = :t "
                    "AND column_default LIKE 'nextval%'"
                ),
                {"t": table},
            )
        ).all()
    except Exception:  # pragma: no cover - permissions
        return {}
    found: dict[str, str] = {}
    for name, default in rows:
        if name not in columns:
            continue
        sequence = _sequence_name(default)
        if sequence:
            found[name] = sequence
    return found


async def python_dump(conn: AsyncConnection) -> dict:
    """Reflect the live database and emit a restorable SQL script.

    Returns ``{"dump": str, "schema": str, "json": {table: bytes},
    "csv": {table: bytes}, "counts": {table: rows}, "sequences": [...],
    "engine": "python"}``.
    """
    dialect = conn.dialect
    pg = dialect.name == "postgresql"

    def _reflect(sync_conn) -> MetaData:
        meta = MetaData()
        try:
            meta.reflect(bind=sync_conn)
        except Exception as exc:  # pragma: no cover - exotic schemas
            log.warning("reflection failed (%s) — falling back to the ORM metadata", exc)
            from ..db.models import Base

            meta = Base.metadata
        return meta

    metadata = await conn.run_sync(_reflect)
    inspector_tables = set(
        await conn.run_sync(lambda sync_conn: inspect(sync_conn).get_table_names())
    )

    ordered = [t for t in metadata.sorted_tables if t.name in inspector_tables]
    ordered += [
        t for t in sorted(inspector_tables - {t.name for t in ordered})
        if t != "alembic_version"
    ]
    # alembic_version last: it is not reflected as a Table when metadata came
    # from the ORM fallback
    if "alembic_version" in inspector_tables and "alembic_version" not in {
        t.name for t in ordered
    }:
        ordered.append(_alembic_table())

    schema_parts: list[str] = []
    dump_parts: list[str] = []
    json_parts: dict[str, bytes] = {}
    csv_parts: dict[str, bytes] = {}
    counts: dict[str, int] = {}
    sequence_resets: list[str] = []

    # NOTE: no `SET TRANSACTION ISOLATION LEVEL` here — SQLAlchemy has already
    # begun the transaction when the first statement runs, and PostgreSQL
    # rejects changing the isolation level at that point. The dump is taken
    # while the bot holds an advisory lock and writes are tiny, so a plain
    # read-committed snapshot is more than enough.

    quoted: list[str] = []
    for table in ordered:
        name = table.name
        quoted.append(dialect.identifier_preparer.format_table(table))
        try:
            schema_parts.append(_compile_ddl(CreateTable(table), dialect))
        except Exception as exc:  # pragma: no cover - exotic column types
            log.warning("could not compile DDL for %s: %s", name, exc)
        for index in sorted(table.indexes, key=lambda ix: ix.name or ""):
            try:
                schema_parts.append(_compile_ddl(CreateIndex(index), dialect))
            except Exception:  # pragma: no cover
                continue

        if name in EXCLUDED_TABLES:
            counts[name] = 0
            continue

        columns = [c.name for c in table.columns]
        if not columns:
            counts[name] = 0
            continue

        result = await conn.execute(table.select())
        rows = [dict(zip(result.keys(), row)) for row in result.fetchall()]
        counts[name] = len(rows)

        col_sql = ", ".join(
            dialect.identifier_preparer.quote(c) for c in columns
        )
        statement_head = (
            f"INSERT INTO {dialect.identifier_preparer.format_table(table)} "
            f"({col_sql}) VALUES\n"
        )
        chunk: list[str] = []
        chunk_bytes = 0
        for row in rows:
            values = "(" + ", ".join(sql_literal(row.get(c)) for c in columns) + ")"
            if chunk and (
                len(chunk) >= INSERT_BATCH_ROWS
                or chunk_bytes + len(values) > _INSERT_BATCH_BYTES
            ):
                dump_parts.append(statement_head + ",\n".join(chunk) + ";")
                chunk, chunk_bytes = [], 0
            chunk.append(values)
            chunk_bytes += len(values) + 2
        if chunk:
            dump_parts.append(statement_head + ",\n".join(chunk) + ";")

        # one catalogue query per table, then a MAX() per serial column
        preparer = dialect.identifier_preparer
        for column, sequence in (await _sequences_of(conn, name, columns)).items():
            maximum = (
                await conn.execute(
                    text(
                        f"SELECT COALESCE(MAX({preparer.quote(column)}), 0) "
                        f"FROM {preparer.format_table(table)}"
                    )
                )
            ).scalar_one()
            quoted_sequence = sequence if '"' in sequence else f"'{sequence}'"
            sequence_resets.append(
                f"SELECT setval({quoted_sequence}, {int(maximum or 0) + 1}, false);"
            )

        json_parts[name] = json.dumps(
            rows, ensure_ascii=False, indent=1, default=json_default
        ).encode("utf-8")

        buffer = io.StringIO()
        writer = csv.writer(buffer, lineterminator="\n")
        writer.writerow(columns)
        for row in rows:
            writer.writerow([csv_text(row.get(c)) for c in columns])
        csv_parts[name] = buffer.getvalue().encode("utf-8")

    # ── assemble the scripts (dialect aware: PostgreSQL is production, but a
    #    dev database may be SQLite and the dump must stay restorable there) ──
    header = _sql_header(counts, dialect.name)
    data_sql = "\n\n".join(dump_parts) if dump_parts else "-- no data"

    if pg:
        prelude = [
            "BEGIN;",
            "SET client_encoding = 'UTF8';",
            "SET standard_conforming_strings = on;",
            "SET check_function_bodies = false;",
            "SET client_min_messages = warning;",
            # data must load without FK/trigger interference; the role that owns
            # the database (Railway's `postgres`) may change this setting.
            "SET session_replication_role = replica;",
        ]
        truncate_target = ", ".join(quoted) if quoted else "nothing"
        clear = [
            f"-- ── existing rows are removed first ({len(quoted)} tables) ──",
            f"TRUNCATE TABLE {truncate_target} RESTART IDENTITY CASCADE;",
        ]
        epilogue = ["SET session_replication_role = DEFAULT;", "COMMIT;"]
        restore_hint = 'psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f dump.sql'
    else:
        # SQLite understands none of the PostgreSQL session commands and has no
        # TRUNCATE; DELETE + deferred FKs give the same result.
        prelude = [
            "PRAGMA foreign_keys = OFF;",
            "BEGIN TRANSACTION;",
        ]
        clear = [
            f"-- ── existing rows are removed first ({len(quoted)} tables) ──",
            *(f"DELETE FROM {name};" for name in reversed(quoted)),
        ]
        epilogue = ["COMMIT;", "PRAGMA foreign_keys = ON;"]
        restore_hint = 'sqlite3 /path/to/database.db ".read dump.sql"'

    schema_sql = "\n\n".join(
        [header, "-- ── schema ──", *schema_parts, *(epilogue[:0])]
    )
    dump_sql = "\n\n".join(
        [
            header,
            *prelude,
            "-- ── schema (created only when missing) ──",
            *schema_parts,
            *clear,
            "-- ── data ──",
            data_sql,
            "-- ── sequences ──",
            *(sequence_resets or ["-- no sequences to reset"]),
            *epilogue,
        ]
    )
    data_only_sql = "\n\n".join(
        [header, *prelude, *clear, "-- ── data ──", data_sql,
         *(sequence_resets or []), *epilogue]
    )

    return {
        "dump": dump_sql,
        "schema": schema_sql,
        "data": data_only_sql,
        "json": json_parts,
        "csv": csv_parts,
        "counts": counts,
        "sequences": sequence_resets,
        "engine": "python",
        "dialect": dialect.name,
        "restore_hint": restore_hint,
    }


def _alembic_table():
    from sqlalchemy import Column, String, Table

    return Table(
        "alembic_version",
        MetaData(),
        Column("version_num", String(32), primary_key=True, nullable=False),
    )


def _safe_db_label(url: str | None) -> str:
    """Database *name* only — an archive travels to Telegram chats, so the dump
    header must never carry a DSN (host, port, user, password or file path)."""
    raw = url or settings.database_url or ""
    tail = raw.rsplit("/", 1)[-1].split("?")[0].strip()
    return tail or "<unnamed database>"


def _sql_header(counts: dict[str, int], dialect: str = "postgresql") -> str:
    from .. import __version__
    from ..domain.persian import jalali_datetime

    total = sum(counts.values())
    hint = (
        'psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f dump.sql'
        if dialect == "postgresql"
        else 'sqlite3 /path/to/database.db ".read dump.sql"'
    )
    return "\n".join(
        [
            "-- ─────────────────────────────────────────────────────────────────────",
            "-- Rotbe Land weekly planner · database backup",
            f"-- app version : {__version__}",
            f"-- created     : {jalali_datetime(_now_utc())} (Asia/Tehran)",
            f"--             : {_now_utc():%Y-%m-%d %H:%M:%S} UTC",
            f"-- database    : {_safe_db_label(settings.database_url)}",
            f"-- dialect     : {dialect}",
            f"-- tables      : {len(counts)} · rows: {total}",
            f"-- restore     : {hint}",
            "-- ─────────────────────────────────────────────────────────────────────",
        ]
    )


# ═════════════════════════════════════════════════════════════ packaging ═══
def _readme_text(meta: dict) -> str:
    return f"""بکاپ پایگاه داده ربات برنامه‌ریز هفتگی رتبه لند
=================================================

تاریخ (شمسی)   : {meta['created_jalali']}
تاریخ (میلادی) : {meta['created_utc']}
نسخه ربات      : {meta['app_version']}
موتور بکاپ     : {meta['engine']}
جدول‌ها        : {meta['tables']}
رکوردها        : {meta['rows']}
حجم آرشیو      : {meta['size']}
sha256         : {meta['sha256']}

محتویات
-------
sql/dump.sql      ← اسکریپ کامل بازگردانی (ساخت جدول + پاک‌سازی + داده + سکونس‌ها)
sql/schema.sql    ← فقط ساختار جدول‌ها
sql/data_only.sql ← فقط داده (بدون CREATE TABLE) برای ریختن داده در جدول‌های موجود
json/*.json       ← داده هر جدول به‌صورت JSON (برای بررسی و دیباگ)
csv/*.csv         ← داده هر جدول به‌صورت CSV (باز شدن در اکسل)
metadata.json     ← جزئیات بکاپ و checksum فایل‌ها
checksums.txt     ← sha256 هر عضو آرشیو
restore.sh        ← اجرای سریع بازگردانی با psql

بازگردانی (روی یک پایگاه داده خالی یا موجود)
--------------------------------------------
۱) آرشیو را باز کنید:
     tar -xzf {meta['filename']}
۲) DATABASE_URL را به‌صورت postgresql:// (نه asyncpg) آماده کنید:
     export DATABASE_URL="postgresql://USER:PASS@HOST:5432/DB"
۳) اجرا:
     psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f {meta['folder']}/sql/dump.sql

نکته‌ها
-------
• dump.sql داده موجود را با TRUNCATE پاک می‌کند و سپس داده بکاپ را می‌ریزد؛
  یعنی بازگردانی روی پایگاه داده فعال = جایگزینی کامل داده.
• جدول‌های bot_settings و backup_logs عمداً در بکاپ نیستند (تنظیمات زمان‌بندی
  از متغیرهای محیطی بازسازی می‌شود و تاریخچه بکاپ بخشی از داده کاری نیست).
• اگر pg_dump روی سرور نصب باشد، فایل sql/pgdump.dump هم داخل آرشیو است؛ در آن
  صورت بهترین راه بازگردانی این است:
     pg_restore --clean --if-exists --no-owner -d "$DATABASE_URL" sql/pgdump.dump
• فایل‌های تولیدشده (PNG/PDF برنامه‌ها) در STORAGE_ROOT قرار دارند و بخشی از
  این آرشیو نیستند؛ آن‌ها از داده پایگاه داده دوباره تولید می‌شوند.
"""


def _restore_script(folder: str) -> str:
    return f"""#!/usr/bin/env sh
# Restore the Rotbe Land database from this backup.
#   export DATABASE_URL="postgresql://user:pass@host:5432/db"
#   sh restore.sh
set -eu

HERE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
DUMP="$HERE/{folder}/sql/dump.sql"
PGDUMP="$HERE/{folder}/sql/pgdump.dump"

if [ -z "${{DATABASE_URL:-}}" ]; then
    echo "DATABASE_URL is not set" >&2
    exit 1
fi

if [ -f "$PGDUMP" ] && command -v pg_restore >/dev/null 2>&1; then
    echo "▶ pg_restore $PGDUMP"
    exec pg_restore --clean --if-exists --no-owner --no-privileges -d "$DATABASE_URL" "$PGDUMP"
fi

echo "▶ psql -f $DUMP"
exec psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f "$DUMP"
"""


def package_backup(payload: dict, *, root: Path, stamp: str, pgdump: Path | None = None) -> BackupArtifact:
    """Build the .tar.gz archive from a dump payload. Blocking → run in a thread."""
    from .. import __version__
    from ..domain.persian import jalali_datetime

    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    folder = f"{APP_NAME}-backup-{stamp}"
    archive = root / f"{folder}.tar.gz"

    counts: dict[str, int] = payload.get("counts", {})
    members: dict[str, str] = {}
    total_rows = sum(counts.values())

    def _add(tar: tarfile.TarFile, name: str, data: bytes) -> None:
        info = tarfile.TarInfo(f"{folder}/{name}")
        info.size = len(data)
        info.mtime = int(time.time())
        info.mode = 0o755 if name.endswith(".sh") else 0o644
        tar.addfile(info, io.BytesIO(data))
        members[name] = _sha256(data)

    meta = {
        "app": APP_NAME,
        "app_version": __version__,
        "filename": archive.name,
        "folder": folder,
        "created_utc": f"{_now_utc():%Y-%m-%dT%H:%M:%SZ}",
        "created_jalali": jalali_datetime(_now_utc()),
        "timezone": settings.timezone,
        "engine": payload.get("engine", "python"),
        "dialect": payload.get("dialect", "postgresql"),
        "database": _masked_dsn(),
        "tables": len(counts),
        "rows": total_rows,
        "row_counts": counts,
        "excluded_tables": sorted(EXCLUDED_TABLES),
        "size": "",  # filled below (archive size is known after closing)
        "sha256": "",
        "members": members,
    }

    with tempfile.TemporaryDirectory(prefix="rotbeland-backup-") as tmp:
        staging = Path(tmp)
        dump_bytes = payload["dump"].encode("utf-8")
        schema_bytes = payload.get("schema", payload["dump"]).encode("utf-8")
        _stage(staging / SQL_DIRNAME / "dump.sql", dump_bytes)
        _stage(staging / SQL_DIRNAME / "schema.sql", schema_bytes)
        if payload.get("data"):
            _stage(staging / SQL_DIRNAME / "data_only.sql", payload["data"].encode("utf-8"))
        if pgdump is not None and pgdump.exists():
            _stage(staging / SQL_DIRNAME / "pgdump.dump", pgdump.read_bytes())
        for name, data in (payload.get("json") or {}).items():
            _stage(staging / "json" / f"{name}.json", data)
        for name, data in (payload.get("csv") or {}).items():
            _stage(staging / "csv" / f"{name}.csv", data)

        checksum_lines = [
            f"{_sha256(data)}  {SQL_DIRNAME}/dump.sql"
            for data in (dump_bytes,)
        ]
        _stage(staging / "checksums.txt", ("\n".join(checksum_lines) + "\n").encode("utf-8"))

        meta_bytes = json.dumps(meta, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8")
        _stage(staging / "metadata.json", meta_bytes)
        readme = _readme_text(meta).encode("utf-8")
        _stage(staging / "README.txt", readme)
        _stage(staging / "restore.sh", _restore_script(folder).encode("utf-8"))

        tmp_archive = archive.with_suffix(".tar.gz.tmp")
        with tarfile.open(tmp_archive, "w:gz", compresslevel=9) as tar:
            for path in sorted(staging.rglob("*")):
                if path.is_file():
                    tar.add(path, arcname=f"{folder}/{path.relative_to(staging)}")
        tmp_archive.replace(archive)

    digest = _sha256(archive.read_bytes())
    size = archive.stat().st_size

    # refresh metadata/checksums now that every member hash is known
    meta["size"] = human_bytes(size)
    meta["sha256"] = digest
    _rewrite_metadata(archive, folder, meta)

    log.info(
        "backup created engine=%s tables=%s rows=%s size=%s path=%s",
        meta["engine"], meta["tables"], total_rows, human_bytes(size), archive.name,
    )
    return BackupArtifact(
        path=archive,
        filename=archive.name,
        size_bytes=size,
        engine=meta["engine"],
        tables=len(counts),
        rows=total_rows,
        sha256=digest,
        duration_ms=int(payload.get("duration_ms", 0)),
        row_counts=counts,
        members=meta["members"],
    )


def _masked_dsn() -> str:
    from ..config import _mask_dsn

    return _mask_dsn(settings.database_url)


def _stage(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def _rewrite_metadata(archive: Path, folder: str, meta: dict) -> None:
    """Replace metadata.json/checksums.txt inside the archive in place."""
    with tempfile.TemporaryDirectory(prefix="rotbeland-repack-") as tmp:
        staging = Path(tmp)
        with tarfile.open(archive, "r:gz") as tar:
            tar.extractall(staging, filter="data")
        members: dict[str, str] = {}
        for path in sorted((staging / folder).rglob("*")):
            if path.is_file():
                rel = str(path.relative_to(staging / folder))
                if rel in ("metadata.json", "checksums.txt"):
                    continue
                members[rel] = _sha256(path.read_bytes())
        meta["members"] = members
        (staging / folder / "metadata.json").write_bytes(
            json.dumps(meta, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8")
        )
        (staging / folder / "checksums.txt").write_bytes(
            ("".join(f"{digest}  {name}\n" for name, digest in sorted(members.items()))).encode("utf-8")
        )
        (staging / folder / "README.txt").write_bytes(_readme_text(meta).encode("utf-8"))
        tmp_archive = archive.with_suffix(".tar.gz.tmp")
        with tarfile.open(tmp_archive, "w:gz", compresslevel=9) as tar:
            for path in sorted((staging / folder).rglob("*")):
                if path.is_file():
                    tar.add(path, arcname=f"{folder}/{path.relative_to(staging / folder)}")
        tmp_archive.replace(archive)


# ══════════════════════════════════════════════════════════════ creation ═══
def _stamp(now: datetime | None = None) -> str:
    """Sortable, collision-proof archive stamp: 20260917-031502-a3f9."""
    import secrets

    moment = now or datetime.now(_tehran())
    return f"{moment.strftime('%Y%m%d-%H%M%S')}-{secrets.token_hex(2)}"


def _tehran():
    from zoneinfo import ZoneInfo

    try:
        return ZoneInfo(settings.timezone)
    except Exception:  # pragma: no cover - broken tzdata
        return timezone.utc


def _lock_engine():
    """A dedicated, unpooled engine used only for PostgreSQL advisory locks.

    Advisory locks are *session* scoped: taking one on a pooled connection and
    returning it to the pool would leak the lock to whoever checks that
    connection out next. A `NullPool` engine is created, used and disposed, so
    the lock dies with it even if the release call is somehow skipped.
    """
    from sqlalchemy.ext.asyncio import create_async_engine
    from sqlalchemy.pool import NullPool

    return create_async_engine(settings.database_url, poolclass=NullPool)


class _AdvisoryLock:
    """Async context manager that holds a PostgreSQL advisory lock.

    The lock is session scoped, so the connection that took it must stay open
    for as long as the lock is needed — it is kept on the instance and closed
    on exit (which also releases the lock server side).
    """

    def __init__(self, key: int, *, blocking: bool = True):
        self.key = int(key)
        self.blocking = blocking
        self._engine = None
        self._conn = None
        self.acquired = False

    async def __aenter__(self) -> bool:
        if not settings.database_url.startswith("postgresql"):
            self.acquired = True          # nothing to serialise on SQLite
            return True
        function = "pg_advisory_lock" if self.blocking else "pg_try_advisory_lock"
        self._engine = _lock_engine()
        try:
            self._conn = await self._engine.connect()
            result = (
                await self._conn.execute(text(f"SELECT {function}(:k)"), {"k": self.key})
            ).scalar_one()
            self.acquired = True if self.blocking else bool(result)
        except Exception as exc:  # pragma: no cover - no permission / no server
            log.warning("advisory lock %s unavailable: %s", self.key, exc)
            await self._close()
            self.acquired = True          # never block a backup because of a lock
            return True
        if not self.acquired:
            await self._close()
        return self.acquired

    async def _close(self) -> None:
        conn, engine = self._conn, self._engine
        self._conn = self._engine = None
        if conn is not None:
            try:
                await conn.close()
            except Exception:  # pragma: no cover
                pass
        if engine is not None:
            try:
                await engine.dispose()
            except Exception:  # pragma: no cover
                pass

    async def __aexit__(self, *_exc) -> None:
        await self._close()


def backup_lock(blocking: bool = False) -> "_AdvisoryLock":
    """`async with backup_lock():` — only one replica backs up at a time."""
    return _AdvisoryLock(LOCK_KEY_BACKUP, blocking=blocking)


def migration_lock() -> "_AdvisoryLock":
    """`async with migration_lock():` — serialises `alembic upgrade head`."""
    return _AdvisoryLock(LOCK_KEY_MIGRATION, blocking=True)


async def with_migration_lock(coro):
    """Run `coro` while holding the migration advisory lock (used by bootstrap)."""
    async with migration_lock():
        return await coro


async def create_backup(
    *,
    root: Path | str | None = None,
    use_pg_dump: bool = True,
    now: datetime | None = None,
) -> BackupArtifact:
    """Take one backup. Async, safe to call from a handler or the scheduler."""
    started = time.perf_counter()
    directory = Path(root or settings.backup_dir)
    directory.mkdir(parents=True, exist_ok=True)

    engine_used = "python"
    pgdump_path: Path | None = None
    warning: str | None = None
    if use_pg_dump and settings.database_url.startswith("postgresql") and _pg_dump_binary():
        try:
            pgdump_path, engine_used = await pg_dump_archive(directory / ".tmp")
        except BackupError as exc:
            warning = str(exc)
            log.warning("pg_dump unavailable (%s) — using the pure-Python dumper", exc)
            engine_used = "python"
            pgdump_path = None
    else:
        engine_used = "python"

    from ..db.session import get_engine

    engine = get_engine()
    async with engine.connect() as conn:
        payload = await python_dump(conn)
    payload["engine"] = engine_used if pgdump_path is not None else "python"
    if engine_used == "pg_dump" and pgdump_path is None:
        payload["engine"] = "python"
    payload["duration_ms"] = int((time.perf_counter() - started) * 1000)
    if warning:
        payload["warning"] = warning

    artifact = await asyncio.to_thread(
        package_backup, payload, root=directory, stamp=_stamp(now), pgdump=pgdump_path
    )
    artifact.duration_ms = payload["duration_ms"]
    artifact.warning = warning
    return artifact


def prune_backups(root: Path | str | None = None, keep: int | None = None) -> list[Path]:
    """Keep the newest `keep` archives on disk, delete the rest."""
    directory = Path(root or settings.backup_dir)
    keep = max(1, int(keep if keep is not None else settings.backup_keep))
    if not directory.exists():
        return []
    archives = sorted(
        (p for p in directory.glob(f"{APP_NAME}-backup-*.tar.gz") if p.is_file()),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    removed: list[Path] = []
    for stale in archives[keep:]:
        try:
            stale.unlink()
            removed.append(stale)
        except OSError as exc:  # pragma: no cover - permissions
            log.warning("could not remove old backup %s: %s", stale, exc)
    for tmp in directory.glob("*.tmp"):
        try:
            if tmp.is_dir():
                shutil.rmtree(tmp, ignore_errors=True)
            else:
                tmp.unlink()
        except OSError:  # pragma: no cover
            pass
    if removed:
        log.info("backup retention: removed %s old archive(s), kept %s", len(removed), keep)
    return removed


# ══════════════════════════════════════════════════════════════ delivery ═══
async def deliver(bot: Bot, artifact: BackupArtifact, chat_ids: list[int], caption: str) -> tuple[list[int], list[int]]:
    """Send the archive to every recipient. Returns (delivered, failed)."""
    ok: list[int] = []
    failed: list[int] = []
    document: FSInputFile | str = FSInputFile(artifact.path)
    if artifact.size_bytes > settings.backup_max_bytes:
        log.warning(
            "backup %s is %s — over Telegram's bot upload limit (%s)",
            artifact.filename, human_bytes(artifact.size_bytes),
            human_bytes(settings.backup_max_bytes),
        )
    for chat_id in chat_ids:
        try:
            await bot.send_document(chat_id, document, caption=caption, parse_mode="HTML")
            ok.append(chat_id)
        except Exception as exc:
            log.warning("backup delivery to %s failed: %s", chat_id, exc)
            failed.append(chat_id)
    return ok, failed


def backup_caption(artifact: BackupArtifact, *, auto: bool) -> str:
    from .. import __version__
    from ..domain.persian import jalali_datetime, to_fa_digits

    top = sorted(artifact.row_counts.items(), key=lambda kv: kv[1], reverse=True)[:5]
    lines = [
        "🗄 <b>بکاپ خودکار پایگاه داده</b>" if auto else "🗄 <b>بکاپ پایگاه داده</b>",
        "",
        f"🕐 {jalali_datetime(_now_utc())}",
        f"📦 <code>{artifact.filename}</code>",
        f"📏 حجم: {human_bytes(artifact.size_bytes)}",
        f"🧩 {to_fa_digits(str(artifact.tables))} جدول · "
        f"{to_fa_digits(str(artifact.rows))} رکورد",
        f"⚙️ موتور: <code>{artifact.engine}</code> · نسخه ربات {__version__}",
        f"⏱ زمان تهیه: {to_fa_digits(str(round(artifact.duration_ms / 1000, 1)))} ثانیه",
    ]
    if top:
        lines += ["", "<b>پرحجم‌ترین جدول‌ها</b>"]
        lines += [
            f"• <code>{name}</code>: {to_fa_digits(str(count))} ردیف" for name, count in top
        ]
    lines += [
        "",
        "🔐 <b>بازگردانی:</b> آرشیو را باز کنید و <code>restore.sh</code> را با "
        "<code>DATABASE_URL</code> اجرا کنید (توضیح کامل در README.txt).",
        f"🧬 sha256: <code>{artifact.sha256[:32]}…</code>",
    ]
    return "\n".join(lines)


# ══════════════════════════════════════════════════════════════ service ═══
class BackupService:
    """One entry point for the panel, the scheduler and the CLI."""

    def __init__(self, session, bot: Bot | None = None):
        self.s = session
        self.bot = bot
        self.settings_repo = SettingsRepository(session)
        self.backups = BackupRepository(session)
        self.audit = AuditRepository(session)

    # ── configuration ────────────────────────────────────────────────────
    async def config(self) -> BotSettings:
        return await self.settings_repo.get()

    async def set_enabled(self, enabled: bool, *, actor_id: int | None = None) -> BotSettings:
        row = await self.settings_repo.get()
        row.backup_enabled = bool(enabled)
        if enabled:
            row.backup_last_error = None
        await self.s.flush()
        await self.audit.log(
            "backup.auto_enabled" if enabled else "backup.auto_disabled",
            actor_id=actor_id,
            detail=f"schedule={row.backup_schedule.value}",
        )
        return row

    async def set_schedule(
        self, schedule: BackupSchedule | str, *, actor_id: int | None = None
    ) -> BotSettings:
        row = await self.settings_repo.get()
        row.backup_schedule = (
            schedule if isinstance(schedule, BackupSchedule) else BackupSchedule(schedule)
        )
        await self.s.flush()
        await self.audit.log(
            "backup.schedule_changed", actor_id=actor_id, detail=row.backup_schedule.value
        )
        return row

    async def set_hour(self, hour: int, *, actor_id: int | None = None) -> BotSettings:
        row = await self.settings_repo.get()
        row.backup_hour = int(hour) % 24
        await self.s.flush()
        await self.audit.log("backup.hour_changed", actor_id=actor_id, detail=str(row.backup_hour))
        return row

    async def set_weekday(self, weekday: int, *, actor_id: int | None = None) -> BotSettings:
        row = await self.settings_repo.get()
        row.backup_weekday = min(6, max(0, int(weekday)))
        await self.s.flush()
        await self.audit.log(
            "backup.weekday_changed", actor_id=actor_id, detail=str(row.backup_weekday)
        )
        return row

    async def set_interval_hours(self, hours: int, *, actor_id: int | None = None) -> BotSettings:
        row = await self.settings_repo.get()
        row.backup_every_hours = min(72, max(1, int(hours)))
        await self.s.flush()
        await self.audit.log(
            "backup.interval_changed", actor_id=actor_id, detail=str(row.backup_every_hours)
        )
        return row

    async def set_keep(self, keep: int, *, actor_id: int | None = None) -> BotSettings:
        row = await self.settings_repo.get()
        row.backup_keep = min(60, max(1, int(keep)))
        await self.s.flush()
        await self.audit.log("backup.retention_changed", actor_id=actor_id, detail=str(row.backup_keep))
        return row

    async def set_recipients(self, chat_ids: list[int], *, actor_id: int | None = None) -> BotSettings:
        row = await self.settings_repo.get()
        row.backup_recipients = ",".join(str(int(c)) for c in chat_ids) or None
        await self.s.flush()
        await self.audit.log(
            "backup.recipients_changed", actor_id=actor_id, detail=row.backup_recipients or "ADMIN_IDS"
        )
        return row

    # ── running ──────────────────────────────────────────────────────────
    async def run(
        self,
        *,
        trigger: str = "manual",
        actor_id: int | None = None,
        send: bool = True,
        bot: Bot | None = None,
        now: datetime | None = None,
    ) -> tuple[BackupArtifact | None, BackupLog]:
        """Create one backup, store it, log it and (optionally) deliver it."""
        row = await self.settings_repo.get()
        bot = bot or self.bot
        recipients = row.recipient_list() if send and bot is not None else []
        started = time.perf_counter()
        try:
            artifact = await create_backup(root=settings.backup_dir, now=now)
        except Exception as exc:
            duration = int((time.perf_counter() - started) * 1000)
            message = f"{type(exc).__name__}: {exc}"
            log.exception("backup failed (trigger=%s)", trigger)
            entry = await self.backups.record(
                status=BackupStatus.FAILED,
                trigger=trigger,
                duration_ms=duration,
                error=message,
                created_by_id=actor_id,
                recipients=",".join(str(c) for c in recipients) or None,
            )
            row.backup_last_error = message[:1000]
            await self.audit.log(
                "backup.failed", actor_id=actor_id, detail=f"{trigger}: {message[:300]}"
            )
            if bot is not None and recipients:
                await _notify_failure(bot, recipients, message, trigger)
            return None, entry

        delivered, failed = [], []
        if bot is not None and recipients:
            delivered, failed = await deliver(
                bot, artifact, recipients, backup_caption(artifact, auto=trigger == "auto")
            )

        await asyncio.to_thread(
            prune_backups, settings.backup_dir, row.backup_keep
        )

        entry = await self.backups.record(
            status=BackupStatus.OK,
            trigger=trigger,
            filename=artifact.filename,
            path=str(artifact.path),
            size_bytes=artifact.size_bytes,
            tables=artifact.tables,
            rows=artifact.rows,
            engine=artifact.engine,
            duration_ms=artifact.duration_ms,
            recipients=",".join(str(c) for c in recipients) or None,
            delivered=len(delivered),
            sha256=artifact.sha256,
            error=(
                None
                if not failed
                else f"delivery failed for: {', '.join(str(c) for c in failed)}"
            ),
            created_by_id=actor_id,
        )
        row.backup_last_run_at = _now_utc()
        row.backup_last_error = None
        await self.audit.log(
            "backup.auto" if trigger == "auto" else "backup.created",
            actor_id=actor_id,
            detail=(
                f"{artifact.filename} rows={artifact.rows} size={artifact.size_bytes} "
                f"engine={artifact.engine} delivered={len(delivered)}/{len(recipients)}"
            ),
        )
        log.info(
            "backup %s done: %s · %s rows · %s · delivered=%s/%s",
            trigger, artifact.filename, artifact.rows,
            human_bytes(artifact.size_bytes), len(delivered), len(recipients),
        )
        return artifact, entry

    # ── panel data ───────────────────────────────────────────────────────
    async def status(self) -> dict:
        row = await self.settings_repo.get()
        latest = await self.backups.latest()
        stats = await self.backups.stats()
        archives = sorted(
            Path(settings.backup_dir).glob(f"{APP_NAME}-backup-*.tar.gz")
            if Path(settings.backup_dir).exists()
            else [],
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        return {
            "settings": row,
            "latest": latest,
            "history": await self.backups.recent(5),
            "stats": stats,
            "archives_on_disk": len(archives),
            "disk_bytes": sum(p.stat().st_size for p in archives),
            "recipients": row.recipient_list(),
            "next_run": next_run(row, _now_utc()),
        }


async def _notify_failure(bot: Bot, chat_ids: list[int], message: str, trigger: str) -> None:
    from ..domain.persian import jalali_datetime

    text_message = (
        "⚠️ <b>بکاپ‌گیری ناموفق بود</b>\n\n"
        f"نوع: {'خودکار' if trigger == 'auto' else 'دستی'}\n"
        f"زمان: {jalali_datetime(_now_utc())}\n"
        f"خطا: <code>{message[:400]}</code>\n\n"
        "برای تلاش مجدد: پنل مدیریت → 🗄 بکاپ‌گیری → «🗄 بکاپ فوری»"
    )
    for chat_id in chat_ids:
        try:
            await bot.send_message(chat_id, text_message, parse_mode="HTML")
        except Exception as exc:  # pragma: no cover
            log.warning("could not notify %s about a failed backup: %s", chat_id, exc)


# ══════════════════════════════════════════════════════════════ schedule ═══
def is_due(
    row: BotSettings,
    now: datetime,
    last_run: datetime | None = None,
    *,
    tz=None,
) -> bool:
    """Pure scheduling logic — everything else in this module is I/O."""
    if not row.backup_enabled:
        return False
    zone = tz or _tehran()
    current = now.astimezone(zone) if now.tzinfo else now.replace(tzinfo=zone)
    previous = (
        (last_run or row.backup_last_run_at).astimezone(zone)
        if (last_run or row.backup_last_run_at)
        else None
    )
    schedule = row.backup_schedule
    try:
        schedule = BackupSchedule(schedule)
    except ValueError:
        schedule = BackupSchedule.DAILY

    if schedule is BackupSchedule.HOURLY:
        return previous is None or (current - previous) >= timedelta(hours=1)

    if schedule is BackupSchedule.HOURS:
        every = max(1, int(row.backup_every_hours or 12))
        return previous is None or (current - previous) >= timedelta(hours=every)

    if schedule is BackupSchedule.WEEKLY:
        from ..domain.calendar import JalaliDate

        if JalaliDate.weekday_index(current.date()) != (row.backup_weekday or 0):
            return False
        if current.hour < (row.backup_hour or 0):
            return False
        if previous is None:
            return True
        return (current - previous) >= timedelta(days=6, hours=20)

    # DAILY (default)
    if current.hour < (row.backup_hour or 0):
        return False
    if previous is None:
        return True
    if previous.date() != current.date():
        return True
    return (current - previous) >= timedelta(hours=20)


def next_run(row: BotSettings, now: datetime | None = None) -> datetime | None:
    """When the scheduler will next take a backup (for the panel)."""
    if not row.backup_enabled:
        return None
    zone = _tehran()
    current = (now or _now_utc()).astimezone(zone)
    schedule = row.backup_schedule
    try:
        schedule = BackupSchedule(schedule)
    except ValueError:
        schedule = BackupSchedule.DAILY

    if schedule is BackupSchedule.HOURLY:
        base = current.replace(minute=0, second=0, microsecond=0)
        return base + timedelta(hours=1)
    if schedule is BackupSchedule.HOURS:
        base = current.replace(minute=0, second=0, microsecond=0)
        return base + timedelta(hours=max(1, int(row.backup_every_hours or 12)))

    candidate = current.replace(
        hour=int(row.backup_hour or 0), minute=0, second=0, microsecond=0
    )
    if schedule is BackupSchedule.DAILY:
        while candidate <= current:
            candidate += timedelta(days=1)
        return candidate

    from ..domain.calendar import JalaliDate

    target = int(row.backup_weekday or 0)
    for _ in range(8):
        if JalaliDate.weekday_index(candidate.date()) == target and candidate > current:
            return candidate
        candidate += timedelta(days=1)
    return candidate


def describe_schedule(row: BotSettings) -> str:
    """Persian, human-readable description of the current schedule."""
    from ..domain.calendar import WEEKDAY_KEYS
    from ..domain.persian import WEEKDAY_FA, to_fa_digits

    schedule = row.backup_schedule
    try:
        schedule = BackupSchedule(schedule)
    except ValueError:
        schedule = BackupSchedule.DAILY
    hour = to_fa_digits(f"{int(row.backup_hour or 0):02d}:00")
    if schedule is BackupSchedule.HOURLY:
        return "هر ساعت"
    if schedule is BackupSchedule.HOURS:
        return f"هر {to_fa_digits(str(int(row.backup_every_hours or 12)))} ساعت"
    if schedule is BackupSchedule.WEEKLY:
        day = WEEKDAY_FA[WEEKDAY_KEYS[min(6, max(0, int(row.backup_weekday or 0)))]]
        return f"هفتگی · {day} ساعت {hour}"
    return f"روزانه · ساعت {hour}"


# ══════════════════════════════════════════════════════════════ scheduler ═
class BackupScheduler:
    """Background task that turns «بکاپ خودکار» into reality.

    Deliberately boring: a loop, a tick interval, a database read, and a
    PostgreSQL advisory lock so two replicas can never back up at the same time.
    Any exception is logged and the loop continues — a failed backup must never
    take the bot down.
    """

    def __init__(
        self,
        bot: Bot,
        sessionmaker,
        *,
        tick_seconds: int | None = None,
        first_delay: float = 45.0,
    ):
        self.bot = bot
        self.sessionmaker = sessionmaker
        self.tick_seconds = int(tick_seconds or int(os.getenv("BACKUP_TICK_SECONDS", "120")))
        self.first_delay = first_delay
        self._task: asyncio.Task | None = None
        self._running = False
        self.last_check: datetime | None = None
        self.last_result: str = "—"

    @property
    def running(self) -> bool:
        return self._running

    async def start(self) -> None:
        if self._task is not None:
            return
        self._running = True
        self._task = asyncio.create_task(self._loop(), name="backup-scheduler")
        log.info(
            "backup scheduler started (tick=%ss, dir=%s)",
            self.tick_seconds, settings.backup_dir,
        )

    async def stop(self) -> None:
        self._running = False
        task, self._task = self._task, None
        if task is None:
            return
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):  # noqa: B014 - shutdown path
            pass
        log.info("backup scheduler stopped")

    async def _loop(self) -> None:
        await asyncio.sleep(self.first_delay)
        while self._running:
            try:
                await self.tick()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("backup scheduler tick failed — continuing")
            try:
                await asyncio.sleep(self.tick_seconds)
            except asyncio.CancelledError:
                raise

    async def tick(self, *, now: datetime | None = None) -> BackupArtifact | None:
        self.last_check = _now_utc()
        async with self.sessionmaker() as session:
            service = BackupService(session, self.bot)
            row = await service.config()
            moment = now or _now_utc()
            if not is_due(row, moment):
                return None
            log.info(
                "automatic backup due (%s) — starting", describe_schedule(row)
            )
        async with backup_lock() as locked:
            if not locked:
                log.info("another replica is already taking the backup — skipping")
                self.last_result = "skipped (another replica)"
                return None
            async with self.sessionmaker() as session:
                service = BackupService(session, self.bot)
                artifact, entry = await service.run(trigger="auto")
                await session.commit()
            self.last_result = (
                f"{entry.filename} · {human_bytes(entry.size_bytes)}"
                if artifact
                else f"failed: {(entry.error or '')[:120]}"
            )
            return artifact


__all__ = [
    "BackupArtifact",
    "BackupError",
    "BackupScheduler",
    "BackupService",
    "LOCK_KEY_BACKUP",
    "LOCK_KEY_MIGRATION",
    "backup_caption",
    "backup_lock",
    "create_backup",
    "deliver",
    "describe_schedule",
    "human_bytes",
    "is_due",
    "next_run",
    "package_backup",
    "pg_dump_archive",
    "prune_backups",
    "migration_lock",
    "python_dump",
    "sql_literal",
    "with_migration_lock",
]
