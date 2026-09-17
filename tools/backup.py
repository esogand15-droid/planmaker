"""Backup / restore helper for operators.

    python -m tools.backup                      # create one archive (BACKUP_DIR)
    python -m tools.backup --out /tmp/x         # create it somewhere else
    python -m tools.backup --status             # schedule, next run, history
    python -m tools.backup --list               # archives on disk
    python -m tools.backup --send 123456789     # create AND send it to a chat
    python -m tools.backup --restore-info a.tar.gz   # what is inside an archive

No Telegram traffic unless `--send` is used; the bot's automatic backups are
configured in the admin panel (🛠 پنل مدیریت → 🧰 بکاپ‌گیری).
"""
from __future__ import annotations

import argparse
import asyncio
import sys
import tarfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.config import settings  # noqa: E402
from app.db.session import dispose_engine, init_engine, session_scope, wait_for_database  # noqa: E402
from app.logging_config import setup_logging  # noqa: E402


async def _create(out: str | None, keep: int | None, no_pg_dump: bool) -> int:
    from app.db.models import BackupStatus
    from app.repositories.repositories import AuditRepository
    from app.services.backup import (
        BackupService,
        create_backup,
        human_bytes,
        prune_backups,
    )

    target = Path(out) if out else settings.backup_dir
    artifact = await create_backup(root=target, use_pg_dump=not no_pg_dump)
    async with session_scope() as s:
        service = BackupService(s)
        removed = prune_backups(target, keep if keep is not None else (await service.config()).backup_keep)
        await service.backups.record(
            status=BackupStatus.OK,
            trigger="cli",
            filename=artifact.filename,
            path=str(artifact.path),
            size_bytes=artifact.size_bytes,
            tables=artifact.tables,
            rows=artifact.rows,
            engine=artifact.engine,
            duration_ms=artifact.duration_ms,
            sha256=artifact.sha256,
        )
        await AuditRepository(s).log(
            "backup.created", detail=f"cli {artifact.filename} rows={artifact.rows}"
        )
    print(f"✔ {artifact.path}")
    print(f"  {human_bytes(artifact.size_bytes)} · {artifact.tables} tables · "
          f"{artifact.rows} rows · engine={artifact.engine} · "
          f"{artifact.duration_ms} ms")
    print(f"  sha256={artifact.sha256}")
    if removed:
        print(f"  retention: removed {len(removed)} older archive(s)")
    return 0


async def _send(chat_ids: list[int], out: str | None, no_pg_dump: bool) -> int:
    from aiogram import Bot

    from app.services.backup import backup_caption, create_backup, deliver

    if not settings.bot_token:
        print("✖ BOT_TOKEN is not set — cannot send anything", file=sys.stderr)
        return 1
    target = Path(out) if out else settings.backup_dir
    artifact = await create_backup(root=target, use_pg_dump=not no_pg_dump)
    bot = Bot(settings.bot_token)
    try:
        ok, failed = await deliver(bot, artifact, chat_ids, backup_caption(artifact, auto=False))
    finally:
        await bot.session.close()
    print(f"✔ sent to {ok}" + (f" · ✖ failed for {failed}" if failed else ""))
    return 0 if ok and not failed else 1


async def _status() -> int:
    from app.services.backup import BackupService, describe_schedule, human_bytes

    async with session_scope() as s:
        service = BackupService(s)
        status = await service.status()
        row = status["settings"]
        print(f"automatic   : {'on' if row.backup_enabled else 'off'} · {describe_schedule(row)}")
        print(f"next run    : {status['next_run'] or '—'}")
        print(f"recipients  : {status['recipients'] or '—'}")
        print(f"retention   : keep {row.backup_keep}")
        print(f"directory   : {settings.backup_dir}")
        print(f"archives    : {status['archives_on_disk']} "
              f"({human_bytes(status['disk_bytes'])})")
        print(f"successful  : {status['stats']['ok']} · failed: {status['stats']['failed']}")
        for entry in status["history"]:
            flag = "✔" if entry.status.value == "ok" else "✖"
            print(f"  {flag} {entry.at:%Y-%m-%d %H:%M} {entry.trigger:<7} "
                  f"{entry.filename or '—'} {human_bytes(entry.size_bytes)} "
                  f"rows={entry.rows} delivered={entry.delivered}"
                  + (f" error={entry.error[:60]}" if entry.error else ""))
        if row.backup_last_error:
            print(f"last error  : {row.backup_last_error[:200]}")
    return 0


def _list_archives() -> int:
    from app.services.backup import human_bytes

    directory = Path(settings.backup_dir)
    if not directory.exists():
        print(f"no backup directory yet: {directory}")
        return 0
    archives = sorted(directory.glob("rotbeland-backup-*.tar.gz"),
                      key=lambda p: p.stat().st_mtime, reverse=True)
    if not archives:
        print(f"no archives in {directory}")
        return 0
    for path in archives:
        stamp = path.stat().st_mtime
        print(f"  {human_bytes(path.stat().st_size):>10}  "
              f"{__import__('datetime').datetime.fromtimestamp(stamp):%Y-%m-%d %H:%M}  "
              f"{path.name}")
    return 0


def _restore_info(archive: str) -> int:
    path = Path(archive)
    if not path.exists():
        print(f"✖ no such file: {path}", file=sys.stderr)
        return 1
    with tarfile.open(path) as tar:
        names = tar.getnames()
        folder = names[0].split("/")[0]
        for member in ("README.txt", "metadata.json"):
            handle = tar.extractfile(f"{folder}/{member}")
            if handle:
                print(f"───── {member} ─────")
                print(handle.read().decode("utf-8"))
        print("───── members ─────")
        for name in names:
            print(" ", name)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(prog="tools.backup", description=__doc__)
    parser.add_argument("--out", default=None, help="archive directory (default BACKUP_DIR)")
    parser.add_argument("--keep", type=int, default=None, help="retention count")
    parser.add_argument("--no-pg-dump", action="store_true",
                        help="force the pure-Python dumper")
    parser.add_argument("--send", type=int, action="append", default=[],
                        metavar="CHAT_ID", help="also send the archive to this chat")
    parser.add_argument("--status", action="store_true", help="show settings + history")
    parser.add_argument("--list", action="store_true", help="list archives on disk")
    parser.add_argument("--restore-info", metavar="ARCHIVE", help="inspect an archive")
    args = parser.parse_args()

    setup_logging()
    if args.restore_info:
        return _restore_info(args.restore_info)
    if args.list:
        return _list_archives()

    async def run() -> int:
        init_engine()
        await wait_for_database()
        try:
            if args.status:
                return await _status()
            if args.send:
                return await _send(args.send, args.out, args.no_pg_dump)
            return await _create(args.out, args.keep, args.no_pg_dump)
        finally:
            await dispose_engine()

    return asyncio.run(run())


if __name__ == "__main__":
    raise SystemExit(main())
