"""access requests

People who open the bot without an invite are recorded here (not as users), so
an admin can deliberately grant them a role from the panel.

Revision ID: 9e411e1e97fa
Revises: af6d2e908bc5
Create Date: 2026-08-16
"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "9e411e1e97fa"
down_revision: str | Sequence[str] | None = "af6d2e908bc5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "access_requests",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("telegram_id", sa.BigInteger(), nullable=False),
        sa.Column("full_name", sa.String(length=120), nullable=False),
        sa.Column("username", sa.String(length=64), nullable=True),
        sa.Column(
            "status",
            sa.Enum("PENDING", "APPROVED", "REJECTED", name="requeststatus",
                    native_enum=False),
            nullable=False,
        ),
        sa.Column("visits", sa.Integer(), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True),
                  server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("handled_by_id", sa.Integer(), nullable=True),
        sa.Column(
            "granted_role",
            sa.Enum("ADMIN", "ADVISOR", "STUDENT", name="role", native_enum=False),
            nullable=True,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.ForeignKeyConstraint(["handled_by_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_access_requests_telegram_id", "access_requests",
                    ["telegram_id"], unique=True)
    op.create_index("ix_access_requests_status", "access_requests", ["status"])


def downgrade() -> None:
    op.drop_index("ix_access_requests_status", table_name="access_requests")
    op.drop_index("ix_access_requests_telegram_id", table_name="access_requests")
    op.drop_table("access_requests")
