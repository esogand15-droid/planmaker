"""Admin CLI: bootstrap users and inspect the system without touching SQL.

    python -m tools.manage init-db
    python -m tools.manage add-advisor "نام مشاور" --telegram-id 12345
    python -m tools.manage add-student "علی رضایی" --advisor 1 --telegram-id 222
    python -m tools.manage link --advisor 1 --student 2
    python -m tools.manage list-users
    python -m tools.manage list-plans [--advisor 1]
    python -m tools.manage audit
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.config import settings  # noqa: E402
from app.db.models import Role  # noqa: E402
from app.db.session import create_all, init_engine, session_scope  # noqa: E402
from app.domain.persian import week_label  # noqa: E402
from app.services.plan_manager import PlanManager  # noqa: E402
from app.repositories.repositories import (  # noqa: E402
    AuditRepository,
    PlanRepository,
    UserRepository,
)


async def cmd_init_db() -> None:
    init_engine()
    await create_all()
    print(f"schema ready · {settings.database_url}")


async def cmd_add_user(name: str, role: Role, telegram_id: int | None, advisor: int | None) -> None:
    init_engine()
    async with session_scope() as s:
        users = UserRepository(s)
        user = await users.create(full_name=name, role=role, telegram_id=telegram_id)
        if advisor:
            await users.link_student(advisor, user.id)
        print(f"created #{user.id} {role.value} {name}")


async def cmd_link(advisor: int, student: int) -> None:
    init_engine()
    async with session_scope() as s:
        await UserRepository(s).link_student(advisor, student)
        print(f"advisor #{advisor} ↔ student #{student}")


async def cmd_list_users() -> None:
    from sqlalchemy import select

    from app.db.models import User

    init_engine()
    async with session_scope() as s:
        rows = (await s.execute(select(User).order_by(User.id))).scalars()
        for u in rows:
            print(f"#{u.id:<4} {u.role.value:<8} tg={u.telegram_id or '-':<12} {u.full_name}")


async def cmd_list_plans(advisor: int | None) -> None:
    init_engine()
    async with session_scope() as s:
        plans = await PlanRepository(s).history(advisor_id=advisor, limit=50)
        for p in plans:
            print(
                f"#{p.id:<4} {p.status.value:<10} v{p.version} "
                f"{week_label(p.week_start, p.week_end):<28} {p.student.full_name}"
            )


async def cmd_cleanup(days: int, dry_run: bool) -> None:
    """Retention: delete plans (and their files) older than `days`."""
    init_engine()
    async with session_scope() as s:
        manager = PlanManager(s)
        if dry_run:
            from datetime import timedelta

            from app.domain.persian import today_local

            cutoff = today_local() - timedelta(days=days)
            stale = await manager.plans.older_than(cutoff)
            print(f"would delete {len(stale)} plan(s) with week_end < {cutoff}")
            return
        plans, files = await manager.purge_older_than(days)
        print(f"deleted {plans} plan(s) and {files} file(s)")


async def cmd_audit() -> None:
    init_engine()
    async with session_scope() as s:
        for entry in await AuditRepository(s).recent(30):
            print(
                f"{entry.at:%Y-%m-%d %H:%M} {entry.action:<18} "
                f"plan={entry.plan_id} actor={entry.actor_id} {entry.detail or ''}"
            )


def main() -> None:
    parser = argparse.ArgumentParser(prog="manage", description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("init-db")

    p = sub.add_parser("add-advisor")
    p.add_argument("name")
    p.add_argument("--telegram-id", type=int)

    p = sub.add_parser("add-student")
    p.add_argument("name")
    p.add_argument("--telegram-id", type=int)
    p.add_argument("--advisor", type=int)

    p = sub.add_parser("link")
    p.add_argument("--advisor", type=int, required=True)
    p.add_argument("--student", type=int, required=True)

    p = sub.add_parser("cleanup", help="delete plans older than N days")
    p.add_argument(
        "--days", type=int, default=None,
        help="defaults to the RETENTION_DAYS environment variable",
    )
    p.add_argument("--dry-run", action="store_true")

    sub.add_parser("list-users")
    p = sub.add_parser("list-plans")
    p.add_argument("--advisor", type=int)
    sub.add_parser("audit")

    args = parser.parse_args()
    if args.cmd == "init-db":
        asyncio.run(cmd_init_db())
    elif args.cmd == "add-advisor":
        asyncio.run(cmd_add_user(args.name, Role.ADVISOR, args.telegram_id, None))
    elif args.cmd == "add-student":
        asyncio.run(cmd_add_user(args.name, Role.STUDENT, args.telegram_id, args.advisor))
    elif args.cmd == "link":
        asyncio.run(cmd_link(args.advisor, args.student))
    elif args.cmd == "list-users":
        asyncio.run(cmd_list_users())
    elif args.cmd == "list-plans":
        asyncio.run(cmd_list_plans(args.advisor))
    elif args.cmd == "cleanup":
        days = args.days if args.days is not None else settings.retention_days
        if days <= 0:
            raise SystemExit(
                "nothing to do: pass --days N or set RETENTION_DAYS (0 = keep forever)"
            )
        asyncio.run(cmd_cleanup(days, args.dry_run))
    elif args.cmd == "audit":
        asyncio.run(cmd_audit())


if __name__ == "__main__":
    main()
