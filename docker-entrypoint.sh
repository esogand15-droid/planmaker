#!/usr/bin/env sh
# Startup sequence: verify runtime → migrate → run.
# Usage: ./docker-entrypoint.sh [bot|migrate|doctor|backup|restore|shell|smoke]
set -e

echo "▶ Rotbe Land weekly planner · $(date -u +%FT%TZ)"

MODE="${1:-bot}"

# 1. runtime verification (never prints secrets).
#    A migration job does not need a bot token — only the bot itself does.
MODE="$MODE" python - <<'PY'
import os
import sys

from PIL import features

sys.path.insert(0, ".")
from app.config import settings
from app.rendering.html_renderer import HtmlRenderer

mode = os.getenv("MODE", "bot")
problems = settings.validate_for_runtime()
if mode != "bot":
    problems = [p for p in problems if "BOT_TOKEN" not in p]
for problem in problems:
    print(f"\u2716 config: {problem}", file=sys.stderr)
if problems:
    sys.exit(1)
print("\u2714 config:", settings.safe_summary())
print("\u2714 pillow/libraqm:", features.check("raqm"))
print("\u2714 chromium:", HtmlRenderer.available())
PY

case "$MODE" in
  migrate)
    echo "▶ alembic upgrade head"
    exec alembic upgrade head
    ;;
  doctor)
    # schema/migration health report — the first thing to run after a bad deploy
    exec python -m tools.manage doctor
    ;;
  backup)
    # one archive in $BACKUP_DIR, no Telegram traffic (the bot does that itself)
    exec python -m tools.backup "${@:2}"
    ;;
  restore)
    # ./docker-entrypoint.sh restore --inspect /data/backups/rotbeland-backup-….tar.gz
    # ./docker-entrypoint.sh restore --restore  /data/backups/rotbeland-backup-….tar.gz
    exec python -m tools.backup "${@:2}"
    ;;
  shell)
    exec /bin/sh
    ;;
  smoke)
    exec python -m tools.smoke_test
    ;;
  bot)
    # Migrations are ON by default now: a service that boots against an empty
    # database used to answer every /start with «relation "users" does not exist».
    # `app.bot.main` also re-checks the schema itself (alembic + verification +
    # self-heal), so this step is a fast, visible belt-and-braces pass.
    if [ "${RUN_MIGRATIONS_ON_START:-true}" = "true" ]; then
      echo "▶ alembic upgrade head"
      alembic upgrade head
    else
      echo "▶ RUN_MIGRATIONS_ON_START=false — skipping alembic (the app still verifies the schema)"
    fi
    echo "▶ starting bot (polling)"
    exec python -m app.bot.main
    ;;
  *)
    exec "$@"
    ;;
esac
