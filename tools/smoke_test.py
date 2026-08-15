"""Post-deploy smoke test — verifies a real deployment end to end.

    python -m tools.smoke_test              # checks config, DB, renderer, Telegram
    python -m tools.smoke_test --full       # + renders a throwaway plan (no DB writes)
    python -m tools.smoke_test --send-to 123456789   # + sends the sample to a chat

Exit code 0 = healthy, 1 = something is broken (safe to use in CI/Railway).
Never prints secrets.
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.config import settings  # noqa: E402
from app.db.session import dispose_engine, init_engine, wait_for_database  # noqa: E402
from app.domain.models import Activity, Assignment, WeeklyPlan  # noqa: E402
from app.domain.persian import saturday_of  # noqa: E402
from app.logging_config import setup_logging  # noqa: E402

OK = "✔"
BAD = "✖"


class Report:
    def __init__(self) -> None:
        self.failures: list[str] = []

    def check(self, name: str, ok: bool, detail: str = "") -> bool:
        print(f"{OK if ok else BAD} {name}{f' — {detail}' if detail else ''}")
        if not ok:
            self.failures.append(name)
        return ok


def sample_plan() -> WeeklyPlan:
    plan = WeeklyPlan(student_name="اسموک تست", student_id="smoke")
    plan.apply_week_start(saturday_of(date.today()))
    plan.day("saturday").set_slot(
        0, Activity.from_quick_entry(0, "زیست | گوارش | ۴۰ تست | ۹۰ دقیقه")
    )
    plan.day("sunday").set_slot(1, Activity(1, subject="ریاضی", topic="تابع", duration="۶۰ دقیقه"))
    plan.assignments.append(Assignment(text="مرور فصل ۲", order=0))
    return plan


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--full", action="store_true", help="also render PNG/PDF")
    parser.add_argument("--send-to", type=int, help="chat id to send the sample plan to")
    args = parser.parse_args()

    setup_logging("WARNING")
    report = Report()
    print("── Rotbe Land · smoke test ───────────────────────────────")
    print(f"   config: {settings.safe_summary()}\n")

    # 1. configuration
    problems = settings.validate_for_runtime()
    report.check("configuration", not problems, "; ".join(problems))

    # 2. assets + shaping
    from PIL import features

    from app.rendering.factory import get_renderer

    report.check("pillow/libraqm shaping", features.check("raqm"))
    renderer = get_renderer(settings.render_backend, settings.template)
    layout = renderer.layout
    report.check("template asset", layout.template_path.exists(), str(layout.template_path))
    report.check(
        "fonts", all(layout.font_path(w).exists() for w in ("regular", "medium", "bold"))
    )
    report.check("renderer backend", True, renderer.signature)

    # 3. storage
    try:
        settings.storage_root.mkdir(parents=True, exist_ok=True)
        probe = settings.storage_root / ".smoke"
        probe.write_bytes(b"ok")
        probe.unlink()
        report.check("storage writable", True, str(settings.storage_root))
    except Exception as exc:
        report.check("storage writable", False, str(exc))

    # 4. database + migrations
    try:
        init_engine()
        await wait_for_database(retries=3, base_delay=0.5)
        from sqlalchemy import text

        from app.db.session import session_scope

        report.check("database connection", True)

        version = None
        try:
            async with session_scope() as s:
                version = (
                    await s.execute(text("SELECT version_num FROM alembic_version"))
                ).scalar_one_or_none()
        except Exception:
            version = None
        report.check(
            "alembic migration applied", bool(version),
            f"revision {version}" if version else "run: alembic upgrade head",
        )

        async with session_scope() as s:
            users = (await s.execute(text("SELECT count(*) FROM users"))).scalar_one()
            advisors = (
                await s.execute(text("SELECT count(*) FROM users WHERE role = 'ADVISOR'"))
            ).scalar_one()
        report.check("advisor exists", advisors > 0,
                     f"{advisors} advisor(s), {users} user(s) — "
                     "create one with: python -m tools.manage add-advisor")
    except Exception as exc:
        report.check("database", False, f"{type(exc).__name__}: {exc}")
    finally:
        await dispose_engine()

    # 5. rendering
    if args.full or args.send_to:
        from app.services.plan_service import WeeklyPlanService

        service = WeeklyPlanService(
            renderer, storage_root=settings.storage_root,
            print_scale=settings.print_scale, pdf_dpi=settings.pdf_dpi,
        )
        plan = sample_plan()
        try:
            result = await asyncio.to_thread(service.generate, plan, force=True)
            report.check(
                "render PNG", result.png_path.stat().st_size > 10_000,
                f"{result.png_path.stat().st_size // 1024} KB via {result.renderer}",
            )
            report.check(
                "render PDF", result.pdf_path.read_bytes()[:5] == b"%PDF-",
                f"{result.pdf_path.stat().st_size // 1024} KB in {result.duration_ms} ms",
            )
        except Exception as exc:
            report.check("render", False, f"{type(exc).__name__}: {exc}")
            result = None
    else:
        result = None

    # 6. Telegram
    if settings.bot_token:
        from aiogram import Bot
        from aiogram.types import FSInputFile

        bot = Bot(settings.bot_token)
        try:
            me = await bot.get_me()
            report.check("telegram getMe", True, f"@{me.username}")
            info = await bot.get_webhook_info()
            report.check(
                "polling mode (no webhook set)", not info.url,
                info.url or "webhook empty — long polling is active",
            )
            if args.send_to and result is not None:
                await bot.send_photo(
                    args.send_to, FSInputFile(result.png_path), caption="🔎 اسموک تست رتبه لند"
                )
                await bot.send_document(args.send_to, FSInputFile(result.pdf_path))
                report.check("telegram delivery", True, f"sent to {args.send_to}")
        except Exception as exc:
            report.check("telegram", False, f"{type(exc).__name__}: {exc}")
        finally:
            await bot.session.close()

    print("──────────────────────────────────────────────────────────")
    if report.failures:
        print(f"{BAD} FAILED: {', '.join(report.failures)}")
        return 1
    print(f"{OK} all checks passed — deployment looks healthy")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
