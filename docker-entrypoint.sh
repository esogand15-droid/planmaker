#!/usr/bin/env sh
# Startup sequence: verify runtime → migrate → run.
# Usage: ./docker-entrypoint.sh [bot|migrate|shell|smoke]
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
  shell)
    exec /bin/sh
    ;;
  smoke)
    exec python -m tools.smoke_test
    ;;
  bot)
    # Migrations run in the release/pre-deploy step on Railway. Set
    # RUN_MIGRATIONS_ON_START=true for single-service setups (compose, VPS).
    if [ "${RUN_MIGRATIONS_ON_START:-false}" = "true" ]; then
      echo "▶ alembic upgrade head"
      alembic upgrade head
    fi
    echo "▶ starting bot (polling)"
    exec python -m app.bot.main
    ;;
  *)
    exec "$@"
    ;;
esac
