"""bot settings + backup log

Adds the runtime configuration row the admin panel edits (automatic backup
schedule, retention, recipients) and the history of every backup attempt.

Revision ID: c1f4b7d20a11
Revises: 9e411e1e97fa
Create Date: 2026-09-17
"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "c1f4b7d20a11"
down_revision: str | Sequence[str] | None = "9e411e1e97fa"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "bot_settings",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("backup_enabled", sa.Boolean(), nullable=False),
        sa.Column(
            "backup_schedule",
            sa.Enum("HOURLY", "HOURS", "DAILY", "WEEKLY",
                    name="backupschedule", native_enum=False),
            nullable=False,
        ),
        sa.Column("backup_hour", sa.Integer(), nullable=False),
        sa.Column("backup_weekday", sa.Integer(), nullable=False),
        sa.Column("backup_every_hours", sa.Integer(), nullable=False),
        sa.Column("backup_keep", sa.Integer(), nullable=False),
        sa.Column("backup_recipients", sa.Text(), nullable=True),
        sa.Column("backup_last_run_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("backup_last_error", sa.Text(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "backup_logs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("at", sa.DateTime(timezone=True),
                  server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("trigger", sa.String(length=16), nullable=False),
        sa.Column(
            "status",
            sa.Enum("OK", "FAILED", name="backupstatus", native_enum=False),
            nullable=False,
        ),
        sa.Column("filename", sa.Text(), nullable=True),
        sa.Column("path", sa.Text(), nullable=True),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("tables", sa.Integer(), nullable=False),
        sa.Column("rows", sa.Integer(), nullable=False),
        sa.Column("engine", sa.String(length=16), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=False),
        sa.Column("recipients", sa.Text(), nullable=True),
        sa.Column("delivered", sa.Integer(), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_by_id", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(["created_by_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_backup_logs_at", "backup_logs", ["at"])
    op.create_index("ix_backup_logs_status", "backup_logs", ["status"])


def downgrade() -> None:
    op.drop_index("ix_backup_logs_status", table_name="backup_logs")
    op.drop_index("ix_backup_logs_at", table_name="backup_logs")
    op.drop_table("backup_logs")
    op.drop_table("bot_settings")
