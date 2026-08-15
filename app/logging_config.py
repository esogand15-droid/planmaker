"""Logging setup with a secret-redacting filter.

Nothing that reaches the log stream may contain the bot token or a database
password — Railway logs are readable by anyone with project access.
"""
from __future__ import annotations

import logging
import re
import sys

from .config import settings

_TOKEN_RE = re.compile(r"\b\d{6,12}:[A-Za-z0-9_-]{30,}\b")
_DSN_RE = re.compile(r"(//[^:/@\s]+):([^@\s]+)@")
_URL_TOKEN_RE = re.compile(r"(api\.telegram\.org/bot)[^/\s]+")


def redact(text: str) -> str:
    text = _TOKEN_RE.sub("<TOKEN>", text)
    text = _DSN_RE.sub(r"\1:***@", text)
    text = _URL_TOKEN_RE.sub(r"\1<TOKEN>", text)
    return text


class RedactingFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        try:
            if isinstance(record.msg, str):
                record.msg = redact(record.msg)
            if record.args:
                if isinstance(record.args, dict):
                    record.args = {k: _redact_any(v) for k, v in record.args.items()}
                else:
                    record.args = tuple(_redact_any(a) for a in record.args)
        except Exception:  # pragma: no cover - logging must never raise
            pass
        return True


def _redact_any(value):
    return redact(value) if isinstance(value, str) else value


def setup_logging(level: str | None = None) -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)-7s %(name)s :: %(message)s")
    )
    handler.addFilter(RedactingFilter())

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level or settings.log_level)

    # third-party noise
    logging.getLogger("aiogram.event").setLevel(logging.WARNING)
    logging.getLogger("aiosqlite").setLevel(logging.WARNING)
    logging.getLogger("sqlalchemy.engine").setLevel(
        logging.INFO if settings.sql_echo else logging.WARNING
    )
