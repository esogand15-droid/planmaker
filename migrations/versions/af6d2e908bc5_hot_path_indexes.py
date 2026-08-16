"""hot path indexes

Admin screens filter users by role, and the drafts list is ordered by
updated_at per advisor. Both were table scans on PostgreSQL.

Revision ID: af6d2e908bc5
Revises: 37248bc77614
Create Date: 2026-08-16
"""
from collections.abc import Sequence

from alembic import op

revision: str = "af6d2e908bc5"
down_revision: str | Sequence[str] | None = "37248bc77614"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index("ix_users_role", "users", ["role"])
    op.create_index("ix_plan_advisor_updated", "weekly_plans", ["advisor_id", "updated_at"])


def downgrade() -> None:
    op.drop_index("ix_plan_advisor_updated", table_name="weekly_plans")
    op.drop_index("ix_users_role", table_name="users")
