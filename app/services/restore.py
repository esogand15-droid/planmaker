"""Restore the whole database from one of the bot's own backup archives.

The archive that `BackupService` produces is designed to be restorable by a
human with `psql` — this module makes the *bot itself* able to do the same job,
from a file an admin uploads in Telegram:

    inspect(path)   → what is inside, is it trustworthy, what will change
    restore(path)   → safety backup → wipe → load → verify → migrate → report

Safety rules that are enforced here and nowhere else:

* **Only an archive this app produced** is accepted: `metadata.json` must carry
  our app name, and every member listed in `checksums.txt` must match its
  recorded sha256. A tampered or truncated file is refused before anything is
  touched.
* **The live database is backed up first** (`trigger="pre_restore"`), and that
  archive is handed back to the caller so it can be sent to the admin — after a
  restore, `backup_logs` belongs to the archive that was loaded, so the only
  durable proof that the restore happened is that file.
* **Every connection is closed before the load.** `TRUNCATE … CASCADE` needs an
  exclusive lock; an open pooled session would deadlock the restore.
* **The load itself is one transaction** (the generated `dump.sql` starts with
  `BEGIN` and ends with `COMMIT`), so a failure halfway leaves the database
  exactly as it was.
* **A newer archive than the installed code is refused.** Restoring data written
  by a newer schema into older code silently corrupts behaviour; the operator
  must deploy first.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
import shutil
import sqlite3
import tarfile
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import unquote, urlparse

from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

from ..config import settings
from .backup import (
    APP_NAME,
    DERIVED_MEMBERS,
    EXCLUDED_TABLES,
    SQL_DIRNAME,
    BackupArtifact,
    _libpq_url,
    _now_utc,
    human_bytes,
)

log = logging.getLogger(__name__)

#: hard ceiling for an uploaded archive (Telegram bots download up to 20 MB)
MAX_RESTORE_BYTES = int(os.getenv("RESTORE_MAX_BYTES", str(60 * 1024 * 1024)))
#: psql/pg_restore are allowed to run long on a big database
_RESTORE_TIMEOUT = int(os.getenv("RESTORE_TIMEOUT", "1800"))
_SQL_TIMEOUT = int(os.getenv("RESTORE_SQL_TIMEOUT", "900"))

DUMP_SQL = f"{SQL_DIRNAME}/dump.sql"
DATA_SQL = f"{SQL_DIRNAME}/data_only.sql"
SCHEMA_SQL = f"{SQL_DIRNAME}/schema.sql"
PGDUMP_BIN = f"{SQL_DIRNAME}/pgdump.dump"

#: Persian labels for the per-table before/after report
TABLE_FA = {
    "users": "کاربران",
    "advisor_students": "اتصال مشاور–دانش‌آموز",
    "weekly_plans": "برنامه‌های هفتگی",
    "plan_days": "روزهای برنامه",
    "activities": "فعالیت‌ها",
    "assignments": "تکالیف",
    "plan_files": "نسخه‌های فایل",
    "access_requests": "درخواست‌های دسترسی",
    "audit_logs": "لاگ رویدادها",
    "bot_settings": "تنظیمات ربات",
    "backup_logs": "تاریخچه بکاپ",
    "alembic_version": "نسخه مهاجرت",
}

#: the tables an admin actually cares about, in report order
KEY_TABLES = (
    "users", "advisor_students", "weekly_plans", "plan_days", "activities",
    "assignments", "plan_files", "access_requests", "audit_logs",
)

#: counted exactly after a restore. `pg_stat_user_tables` is only an estimate
#: right after a bulk load, so anything reported to the admin is re-counted.
VERIFY_TABLES = KEY_TABLES + ("bot_settings", "alembic_version")


class RestoreError(Exception):
    """User-facing restore failure; the detail is safe to show an admin."""


@dataclass
class TableDelta:
    """One row of the «what will change» report."""

    table: str
    now: int | None
    backup: int | None

    @property
    def label(self) -> str:
        return TABLE_FA.get(self.table, self.table)

    @property
    def delta(self) -> int | None:
        if self.now is None or self.backup is None:
            return None
        return self.backup - self.now


@dataclass
class RestorePlan:
    """Everything the UI needs to show *before* anything is destroyed."""

    path: Path
    folder: str
    meta: dict
    sha256: str
    size_bytes: int
    members: dict[str, str]
    #: which member will be executed, and with what tool
    script: str
    executor: str
    now_counts: dict[str, int] = field(default_factory=dict)
    archive_counts: dict[str, int] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    #: set when the archive must not be loaded
    fatal: str | None = None

    # ── facts read out of the archive ────────────────────────────────────
    @property
    def created_utc(self) -> str:
        return str(self.meta.get("created_utc") or "")

    @property
    def created_jalali(self) -> str:
        return str(self.meta.get("created_jalali") or "")

    @property
    def app_version(self) -> str:
        return str(self.meta.get("app_version") or "?")

    @property
    def dialect(self) -> str:
        return str(self.meta.get("dialect") or "postgresql")

    @property
    def engine(self) -> str:
        return str(self.meta.get("engine") or "python")

    @property
    def rows(self) -> int:
        return int(self.meta.get("rows") or 0)

    @property
    def tables(self) -> int:
        return int(self.meta.get("tables") or 0)

    @property
    def revision(self) -> str | None:
        value = self.meta.get("alembic_revision")
        return str(value) if value else None

    def deltas(self) -> list[TableDelta]:
        """Per-table comparison for the tables that matter, in report order."""
        out: list[TableDelta] = []
        for table in KEY_TABLES:
            if table not in self.archive_counts and table not in self.now_counts:
                continue
            out.append(
                TableDelta(table, self.now_counts.get(table), self.archive_counts.get(table))
            )
        return out


@dataclass
class RestoreResult:
    """Outcome of `RestoreService.restore()`."""

    plan: RestorePlan
    ok: bool
    executor: str
    duration_ms: int
    #: per-table counts measured *after* the load
    verified: dict[str, int] = field(default_factory=dict)
    mismatches: list[str] = field(default_factory=list)
    revision_after: str | None = None
    migrated: bool = False
    safety_backup: BackupArtifact | None = None
    error: str | None = None


# ══════════════════════════════════════════════════════════ archive reader ══
def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _async_dsn() -> str:
    """The app's own DSN, normalised (libpq query args stripped)."""
    from ..config import normalize_database_url

    return normalize_database_url(settings.database_url)


def _open_tar(path: Path) -> tarfile.TarFile:
    try:
        return tarfile.open(path, "r:gz")
    except tarfile.ReadError as exc:
        raise RestoreError(
            "این فایل یک آرشیو بکاپ معتبر نیست (tar.gz باز نشد)."
        ) from exc
    except OSError as exc:  # pragma: no cover - unreadable file
        raise RestoreError(f"فایل خوانده نشد: {exc}") from exc


def _member_map(tar: tarfile.TarFile) -> dict[str, str]:
    """`{'metadata.json': 'rotbeland-backup-…/metadata.json', …}`"""
    out: dict[str, str] = {}
    for info in tar.getmembers():
        if not info.isfile():
            continue
        parts = Path(info.name).parts
        if len(parts) < 2:
            continue
        out["/".join(parts[1:])] = info.name
    return out


def _read_member(tar: tarfile.TarFile, full_name: str) -> bytes:
    handle = tar.extractfile(full_name)
    if handle is None:  # pragma: no cover - not a regular file
        raise RestoreError(f"عضو آرشیو خوانده نشد: {full_name}")
    return handle.read()


def _parse_checksums(data: bytes) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in data.decode("utf-8", "replace").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        digest, _, name = line.partition("  ")
        if not name:
            digest, _, name = line.partition(" ")
        if digest and name:
            out[name.strip().lstrip("*")] = digest.strip().lower()
    return out


#: `INSERT INTO alembic_version (version_num) VALUES ('abc…');`
_REVISION_INSERT = re.compile(
    r"INSERT\s+INTO\s+(?:public\.)?\"?alembic_version\"?\s*\([^)]*\)\s*VALUES\s*\(\s*'([^']+)'",
    re.IGNORECASE,
)
#: a `COPY public.alembic_version (version_num) FROM stdin;` block
_REVISION_COPY = re.compile(
    r"COPY\s+(?:public\.)?\"?alembic_version\"?[^\n]*FROM\s+stdin;\s*\n([^\n\\]+)",
    re.IGNORECASE,
)


def _revision_in_sql(sql: str) -> str | None:
    match = _REVISION_INSERT.search(sql)
    if match:
        return match.group(1).strip()
    match = _REVISION_COPY.search(sql)
    if match:
        return match.group(1).strip()
    return None


def _revision_in_json(raw: bytes) -> str | None:
    try:
        rows = json.loads(raw.decode("utf-8"))
    except Exception:  # pragma: no cover - malformed json member
        return None
    for row in rows if isinstance(rows, list) else []:
        if isinstance(row, dict) and row.get("version_num"):
            return str(row["version_num"])
    return None


# ══════════════════════════════════════════════════════════════ migration ══
def _installed_revisions() -> tuple[str | None, dict[str, int]]:
    """(head revision, {revision: distance from the base})."""
    try:
        from alembic.config import Config
        from alembic.script import ScriptDirectory
    except ImportError:  # pragma: no cover - alembic is a hard dependency
        return None, {}
    root = Path(__file__).resolve().parents[2]
    ini = root / "alembic.ini"
    if not ini.exists():  # pragma: no cover - unusual layout
        return None, {}
    config = Config(str(ini))
    config.config_file_name = None
    config.set_main_option("script_location", str(root / "migrations"))
    try:
        script = ScriptDirectory.from_config(config)
        head = script.get_current_head()
    except Exception as exc:  # pragma: no cover - broken migration tree
        log.warning("cannot read the migration tree (%s)", exc)
        return None, {}

    order: dict[str, int] = {}
    for index, revision in enumerate(script.walk_revisions()):
        # walk_revisions yields newest → oldest
        order[str(revision.revision)] = -index
    return head, order


def _revision_problem(plan_revision: str | None, head: str | None,
                      order: dict[str, int]) -> str | None:
    """Refuse an archive written by a *newer* schema than the installed code."""
    if not plan_revision or not head or not order:
        return None
    if plan_revision == head:
        return None
    if plan_revision not in order:
        return (
            f"نسخه مهاجرت داخل بکاپ ({plan_revision[:12]}) در کد نصب‌شده شناخته "
            f"نیست؛ یا بکاپ از نسخه جدیدتری است یا تاریخچه مهاجرت تغییر کرده."
        )
    if order[plan_revision] > order[head]:
        return (
            f"بکاپ از یک پایگاه داده جدیدتر گرفته شده (revision {plan_revision[:12]}) "
            f"ولی کد نصب‌شده روی {head[:12]} است. اول ربات را به همان نسخه ارتقا "
            f"دهید، بعد بازگردانی کنید."
        )
    return None


# ═══════════════════════════════════════════════════════════════ service ═══
class RestoreService:
    """Reads, validates and applies the bot's own backup archives."""

    def __init__(self, factory: async_sessionmaker | None = None):
        self._factory = factory

    # ── inspection ───────────────────────────────────────────────────────
    async def inspect(self, path: Path | str, *, strict: bool = True) -> RestorePlan:
        """Validate an archive and describe exactly what restoring it would do.

        Never writes anything. `strict=False` (CLI) downgrades checksum
        problems to warnings.
        """
        path = Path(path)
        if not path.exists():
            raise RestoreError(f"فایل پیدا نشد: {path}")
        size = path.stat().st_size
        if size == 0:
            raise RestoreError("فایل خالی است.")
        if size > MAX_RESTORE_BYTES:
            raise RestoreError(
                f"حجم فایل ({human_bytes(size)}) از سقف مجاز "
                f"({human_bytes(MAX_RESTORE_BYTES)}) بیشتر است."
            )

        plan = await asyncio.to_thread(self._inspect_sync, path, size, strict)

        # comparing with the live database needs the event loop (async driver)
        try:
            plan.now_counts = await self._live_counts()
        except Exception as exc:
            plan.fatal = f"پایگاه داده فعلی خوانده نشد: {type(exc).__name__}: {exc}"
            return plan

        self._dialect_warnings(plan)
        head, order = _installed_revisions()
        problem = _revision_problem(plan.revision, head, order)
        if problem:
            plan.fatal = problem
        elif plan.revision and head and plan.revision != head:
            plan.warnings.append(
                f"بکاپ روی revision «{plan.revision[:12]}» است و کد فعلی "
                f"«{head[:12]}»؛ بعد از بازگردانی، مهاجرت‌ها خودکار اجرا می‌شوند."
            )
        if not plan.archive_counts.get("users"):
            plan.warnings.append(
                "در این بکاپ هیچ کاربری ثبت نشده است — بعد از بازگردانی، پنل "
                "مدیریت و مشاوران خالی خواهند بود."
            )
        return plan

    def _inspect_sync(self, path: Path, size: int, strict: bool) -> RestorePlan:
        with _open_tar(path) as tar:
            members = _member_map(tar)
            if "metadata.json" not in members:
                raise RestoreError(
                    "این آرشیو metadata.json ندارد؛ بکاپِ همین ربات نیست."
                )
            try:
                meta = json.loads(_read_member(tar, members["metadata.json"]).decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise RestoreError(f"metadata.json خراب است: {exc}") from exc

            if str(meta.get("app") or "") != APP_NAME:
                raise RestoreError(
                    f"این آرشیو بکاپِ «{APP_NAME}» نیست (app={meta.get('app')!r})."
                )

            checksums = (
                _parse_checksums(_read_member(tar, members["checksums.txt"]))
                if "checksums.txt" in members
                else {}
            )
            warnings: list[str] = []
            verified_members = 0
            if not checksums:
                warnings.append("checksums.txt در آرشیو نیست؛ صحت فایل‌ها تأیید نشد.")
            else:
                for name, digest in sorted(checksums.items()):
                    if name in DERIVED_MEMBERS:
                        # generated from metadata.json on every pack — never hashed
                        continue
                    if name not in members:
                        warnings.append(f"عضو ثبت‌شده در آرشیو نیست: {name}")
                        continue
                    data = _read_member(tar, members[name])
                    if _sha256_bytes(data) != digest.lower():
                        message = f"checksum عضو «{name}» مطابقت ندارد — فایل دست‌خورده یا ناقص است."
                        if strict:
                            raise RestoreError(message)
                        warnings.append(message)
                        continue
                    verified_members += 1
                if not verified_members:
                    warnings.append("هیچ عضوی برای بررسی صحت پیدا نشد.")

            # ── what will be executed ───────────────────────────────────
            archive_counts = {
                str(k): int(v) for k, v in (meta.get("row_counts") or {}).items()
            }
            script, executor = self._choose_script(members, meta)
            revision = None
            if DUMP_SQL in members:
                sql = _read_member(tar, members[DUMP_SQL]).decode("utf-8", "replace")
                revision = _revision_in_sql(sql)
            if revision is None and "json/alembic_version.json" in members:
                revision = _revision_in_json(
                    _read_member(tar, members["json/alembic_version.json"])
                )
            if revision is None and DATA_SQL in members:
                revision = _revision_in_sql(
                    _read_member(tar, members[DATA_SQL]).decode("utf-8", "replace")
                )
            if revision:
                meta = dict(meta, alembic_revision=revision)

        plan = RestorePlan(
            path=path,
            folder=str(meta.get("folder") or path.name.removesuffix(".tar.gz")),
            meta=meta,
            sha256=_sha256_bytes(path.read_bytes()),
            size_bytes=size,
            members=members,
            script=script,
            executor=executor,
            archive_counts=archive_counts,
            warnings=warnings,
        )

        return plan

    @staticmethod
    def _choose_script(members: dict[str, str], meta: dict) -> tuple[str, str]:
        """Prefer the transactional SQL script; fall back to pg_restore."""
        if DUMP_SQL in members:
            return DUMP_SQL, "psql" if shutil.which("psql") else "sqlalchemy"
        if PGDUMP_BIN in members and shutil.which("pg_restore"):
            return PGDUMP_BIN, "pg_restore"
        if DATA_SQL in members:
            return DATA_SQL, "psql" if shutil.which("psql") else "sqlalchemy"
        raise RestoreError(
            "در آرشیو هیچ اسکریپ بازگردانی پیدا نشد "
            f"(نه {DUMP_SQL}، نه {PGDUMP_BIN})."
        )

    @staticmethod
    def _dialect_warnings(plan: RestorePlan) -> None:
        from ..config import settings as _settings

        target = "sqlite" if _settings.is_sqlite else "postgresql"
        if plan.dialect != target:
            plan.fatal = (
                f"بکاپ روی {plan.dialect} گرفته شده ولی پایگاه داده فعلی {target} "
                f"است؛ اسکریپ بازگردانی این دو با هم سازگار نیست."
            )
            return
        if target == "sqlite" and plan.executor in ("psql", "pg_restore"):
            plan.executor = "sqlalchemy"

    async def _live_counts(self) -> dict[str, int]:
        from ..db.session import get_engine

        engine = get_engine()
        async with engine.connect() as conn:
            dialect = conn.dialect.name
            if dialect == "postgresql":
                rows = (await conn.execute(text(
                    "SELECT relname, n_live_tup FROM pg_stat_user_tables"
                ))).all()
                counts = {str(name): int(value or 0) for name, value in rows}
            else:
                names = (await conn.execute(text(
                    "SELECT name FROM sqlite_master WHERE type='table' "
                    "AND name NOT LIKE 'sqlite_%'"
                ))).scalars().all()
                counts = {}
                for name in names:
                    counts[str(name)] = int(
                        (await conn.execute(text(f'SELECT COUNT(*) FROM "{name}"'))).scalar_one()
                    )
        return counts

    # ── the destructive part ─────────────────────────────────────────────
    async def restore(
        self,
        path: Path | str,
        *,
        actor_id: int | None = None,
        safety_backup: bool = True,
        run_migrations: bool = True,
        progress=None,
    ) -> RestoreResult:
        """Wipe the live database and load the archive, atomically.

        `progress` is an optional ``async def (str) -> None`` callback used to
        keep the admin informed on long restores.
        """
        async def say(message: str) -> None:
            log.info("restore: %s", message)
            if progress is not None:
                try:
                    await progress(message)
                except Exception as exc:  # pragma: no cover - UI failure
                    log.warning("restore progress callback failed: %s", exc)

        started = time.monotonic()
        plan = await self.inspect(path)
        if plan.fatal:
            raise RestoreError(plan.fatal)

        await say("در حال گرفتن بکاپ اطمینان از وضعیت فعلی…")
        safety: BackupArtifact | None = None
        if safety_backup:
            from .backup import create_backup

            try:
                # the pure-Python dumper: it is dialect-agnostic, needs no
                # external binary and is fast enough to never delay a restore
                safety = await create_backup(root=settings.backup_dir, use_pg_dump=False)
            except Exception as exc:
                # never block a restore on the safety net, but say so loudly
                log.error("pre-restore backup failed: %s", exc)
                plan.warnings.append(f"بکاپ اطمینان گرفته نشد: {exc}")

        await say("بستن اتصال‌ها و بارگذاری بکاپ…")
        executor, error = await self._apply(plan)
        duration_ms = int((time.monotonic() - started) * 1000)
        if error:
            await self._reinit()
            await self._ensure_settings_row()
            await self._log_audit(
                "restore.failed", actor_id,
                f"file={plan.path.name} sha256={plan.sha256[:16]} "
                f"executor={executor} error={error[:400]}",
            )
            return RestoreResult(
                plan=plan, ok=False, executor=executor, duration_ms=duration_ms,
                safety_backup=safety, error=error,
            )

        await say("بازسازی اتصال‌ها و بررسی نتیجه…")
        await self._reinit()
        await self._ensure_settings_row()

        migrated = False
        revision_after: str | None = None
        if run_migrations:
            try:
                from ..db.bootstrap import current_revision, ensure_schema

                report = await ensure_schema()
                migrated = bool(report.get("migrated"))
                revision_after = report.get("revision") or await current_revision()
            except Exception as exc:
                plan.warnings.append(f"مهاجرت بعد از بازگردانی اجرا نشد: {exc}")

        # `restore.started` is written *after* the load on purpose: the load
        # wipes audit_logs, so a row written beforehand would not survive and the
        # history would only ever show successes.
        await self._log_audit(
            "restore.started", actor_id,
            f"file={plan.path.name} sha256={plan.sha256[:16]} rows={plan.rows} "
            f"safety={safety.filename if safety else '—'}",
        )
        verified = await self._verify(plan)
        mismatches = [
            f"{TABLE_FA.get(table, table)}: انتظار {expected}، موجود {verified.get(table)}"
            for table, expected in sorted(plan.archive_counts.items())
            if table in verified
            and verified[table] != expected
            # the restore itself writes audit rows, so `audit_logs` is expected
            # to hold *at least* what the archive carried
            and not (table == "audit_logs" and verified[table] > expected)
            # older archives deliberately carried no settings row; `_ensure_settings_row`
            # re-creates it afterwards, so "1 instead of 0" is the intended state
            and not (table == "bot_settings" and verified[table] == 1)
        ]
        await self._log_audit(
            "restore.completed" if not mismatches else "restore.completed_with_diff",
            actor_id,
            f"file={plan.path.name} executor={executor} rows={plan.rows} "
            f"revision={revision_after or '—'} mismatches={len(mismatches)}",
        )
        return RestoreResult(
            plan=plan, ok=True, executor=executor, duration_ms=duration_ms,
            verified=verified, mismatches=mismatches,
            revision_after=revision_after, migrated=migrated, safety_backup=safety,
        )

    # ── internals ────────────────────────────────────────────────────────
    async def _apply(self, plan: RestorePlan) -> tuple[str, str | None]:
        """Load the archive. Returns (executor actually used, error or None)."""
        from ..db.session import dispose_engine, get_engine

        with tempfile.TemporaryDirectory(prefix="rotbeland-restore-") as tmp:
            staging = Path(tmp)
            with _open_tar(plan.path) as tar:
                for member in tar.getmembers():
                    # `filter="tar"` blocks absolute paths, `..` traversal and
                    # links, but — unlike `filter="data"` — keeps the mode bits,
                    # so the extracted `restore.sh` stays executable for whoever
                    # unpacks the archive by hand afterwards.
                    tar.extract(member, staging, filter="tar")
                script_path = staging / plan.folder / "restore.sh"
                if script_path.exists():
                    script_path.chmod(0o755)
            script = staging / plan.folder / plan.script
            if not script.exists():
                matches = list(staging.rglob(Path(plan.script).name))
                if not matches:
                    return plan.executor, f"اسکریپ {plan.script} در آرشیو پیدا نشد."
                script = matches[0]

            # Every pooled connection must be gone before TRUNCATE … CASCADE,
            # otherwise the load waits on our own sessions and deadlocks.
            await dispose_engine()

            executor = plan.executor
            try:
                if executor == "psql":
                    if not shutil.which("psql"):
                        executor = "sqlalchemy"
                    else:
                        return executor, await self._run_psql(script)
                if executor == "pg_restore":
                    if not shutil.which("pg_restore"):
                        return "sqlalchemy", await self._run_sql_inprocess(script, plan)
                    return executor, await self._run_pg_restore(script)
                return "sqlalchemy", await self._run_sql_inprocess(script, plan)
            except asyncio.CancelledError:  # pragma: no cover
                raise
            except Exception as exc:
                log.exception("restore failed while applying %s", plan.script)
                return executor, f"{type(exc).__name__}: {exc}"[:800]
            finally:
                # bring the pool back whatever happened — the bot keeps serving
                get_engine()

    async def _run_psql(self, script: Path) -> str | None:
        dsn = _libpq_url()
        parsed = urlparse(dsn)
        env = dict(os.environ)
        if parsed.password:
            env["PGPASSWORD"] = unquote(parsed.password)
        env.update({
            "PGCLIENTENCODING": "UTF8",
            "PGCONNECT_TIMEOUT": "20",
            "ON_ERROR_STOP": "1",
        })
        cmd = [
            "psql", "--no-psqlrc", "--quiet", "--no-align",
            "-v", "ON_ERROR_STOP=1", "-f", str(script), dsn,
        ]
        proc = await asyncio.create_subprocess_exec(
            *cmd, env=env,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        try:
            _out, err = await asyncio.wait_for(proc.communicate(), timeout=_RESTORE_TIMEOUT)
        except asyncio.TimeoutError:  # pragma: no cover - pathological restore
            proc.kill()
            return f"psql بعد از {_RESTORE_TIMEOUT} ثانیه متوقف شد."
        if proc.returncode != 0:
            detail = (err or b"").decode("utf-8", "replace").strip().splitlines()
            tail = " · ".join(line for line in detail[-3:] if line.strip())
            return f"psql با کد {proc.returncode} شکست خورد: {tail or 'بدون جزئیات'}"
        return None

    async def _run_pg_restore(self, script: Path) -> str | None:
        dsn = _libpq_url()
        parsed = urlparse(dsn)
        env = dict(os.environ)
        if parsed.password:
            env["PGPASSWORD"] = unquote(parsed.password)
        env["PGCONNECT_TIMEOUT"] = "20"
        cmd = [
            "pg_restore", "--clean", "--if-exists", "--no-owner", "--no-privileges",
            "--single-transaction", "-d", dsn, str(script),
        ]
        proc = await asyncio.create_subprocess_exec(
            *cmd, env=env,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        try:
            _out, err = await asyncio.wait_for(proc.communicate(), timeout=_RESTORE_TIMEOUT)
        except asyncio.TimeoutError:  # pragma: no cover
            proc.kill()
            return f"pg_restore بعد از {_RESTORE_TIMEOUT} ثانیه متوقف شد."
        if proc.returncode != 0:
            detail = (err or b"").decode("utf-8", "replace").strip().splitlines()
            return f"pg_restore با کد {proc.returncode}: {' · '.join(detail[-3:]) or '—'}"
        return None

    async def _run_sql_inprocess(self, script: Path, plan: RestorePlan) -> str | None:
        """Execute the dump inside this process (no PostgreSQL client needed)."""
        sql = script.read_text(encoding="utf-8")
        if settings.is_sqlite:
            return await asyncio.to_thread(self._sqlite_executescript, sql)

        from sqlalchemy.ext.asyncio import create_async_engine

        engine = create_async_engine(_async_dsn())
        try:
            # AUTOCOMMIT so the script's own BEGIN/COMMIT is what delimits the
            # transaction — SQLAlchemy must not wrap it in another one.
            async with engine.connect().execution_options(
                isolation_level="AUTOCOMMIT"
            ) as conn:
                await asyncio.wait_for(
                    conn.exec_driver_sql(sql), timeout=_SQL_TIMEOUT
                )
        except asyncio.TimeoutError:  # pragma: no cover
            return f"بارگذاری اسکریپ بعد از {_SQL_TIMEOUT} ثانیه ناتمام ماند."
        except Exception as exc:
            return f"{type(exc).__name__}: {exc}"[:800]
        finally:
            await engine.dispose()
        return None

    @staticmethod
    def _sqlite_executescript(sql: str) -> str | None:
        url = settings.database_url
        database = url.split("///")[-1].split("?")[0]
        if not database or database == ":memory:":
            return "برای SQLite فقط پایگاه داده فایل‌پایه قابل بازگردانی است."
        try:
            connection = sqlite3.connect(database, timeout=60)
            try:
                connection.executescript(sql)
                connection.commit()
            finally:
                connection.close()
        except Exception as exc:
            return f"{type(exc).__name__}: {exc}"[:800]
        return None

    async def _reinit(self) -> None:
        from ..db.session import get_engine, init_engine, wait_for_database

        init_engine()
        get_engine()
        try:
            await wait_for_database(retries=3)
        except Exception as exc:
            log.error("database still unreachable after restore: %s", exc)

    async def _verify(self, plan: RestorePlan) -> dict[str, int]:
        counts = await self._live_counts()
        # `pg_stat_user_tables` is an estimate right after a bulk load — re-read
        # the tables that matter with an exact COUNT(*) when they disagree.
        exact: dict[str, int] = {}
        from ..db.session import get_engine

        engine = get_engine()
        async with engine.connect() as conn:
            for table in VERIFY_TABLES:
                try:
                    exact[table] = int(
                        (await conn.execute(text(f'SELECT COUNT(*) FROM "{table}"'))).scalar_one()
                    )
                except Exception as exc:
                    log.warning("verify: cannot count %s (%s)", table, exc)
        merged = dict(counts)
        merged.update(exact)
        return merged

    async def _ensure_settings_row(self) -> None:
        """`bot_settings` must have its single row after a restore.

        Archives produced before settings were included in the dump leave the
        table empty (it was TRUNCATEd with everything else); `SettingsRepository`
        re-creates the row from the environment, and we do it right away so the
        backup panel and the scheduler never see an empty table.
        """
        from ..db.session import get_sessionmaker
        from ..repositories.repositories import SettingsRepository

        try:
            factory = self._factory or get_sessionmaker()
            async with factory() as session:
                await SettingsRepository(session).get()
                await session.commit()
        except Exception as exc:
            log.warning("could not re-create the bot_settings row: %s", exc)

    async def _log_audit(self, action: str, actor_id: int | None, detail: str) -> None:
        """Write an audit row through a *fresh* session (the pool may be new)."""
        from ..db.session import get_sessionmaker
        from ..repositories.repositories import AuditRepository

        try:
            factory = self._factory or get_sessionmaker()
            async with factory() as session:
                await AuditRepository(session).log(action, actor_id=actor_id, detail=detail)
                await session.commit()
        except Exception as exc:
            log.warning("could not write audit row %s: %s", action, exc)


# ════════════════════════════════════════════════════════════════ reports ══
def plan_summary(plan: RestorePlan) -> dict:
    """Compact, UI-ready facts about an inspected archive."""
    return {
        "filename": plan.path.name,
        "created_jalali": plan.created_jalali,
        "created_utc": plan.created_utc,
        "app_version": plan.app_version,
        "dialect": plan.dialect,
        "engine": plan.engine,
        "tables": plan.tables,
        "rows": plan.rows,
        "size": human_bytes(plan.size_bytes),
        "sha256": plan.sha256,
        "executor": plan.executor,
        "script": plan.script,
        "revision": plan.revision,
        "deltas": [
            {"table": d.table, "label": d.label, "now": d.now, "backup": d.backup,
             "delta": d.delta}
            for d in plan.deltas()
        ],
        "warnings": list(plan.warnings),
        "fatal": plan.fatal,
        "excluded": sorted(EXCLUDED_TABLES),
        "now_utc": f"{_now_utc():%Y-%m-%dT%H:%M:%SZ}",
    }
