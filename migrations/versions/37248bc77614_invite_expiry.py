"""invite expiry and issue timestamps

Adds expiration/issue tracking to the one-time student invite tokens so a link
cannot be replayed forever, and widens the token column.

Revision ID: 37248bc77614
Revises: 0b0794a592a8
Create Date: 2026-08-16
"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "37248bc77614"
down_revision: str | Sequence[str] | None = "0b0794a592a8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("users") as batch:
        batch.add_column(
            sa.Column("invite_expires_at", sa.DateTime(timezone=True), nullable=True)
        )
        batch.add_column(
            sa.Column("invite_issued_at", sa.DateTime(timezone=True), nullable=True)
        )
        batch.alter_column(
            "invite_token",
            existing_type=sa.String(length=32),
            type_=sa.String(length=64),
            existing_nullable=True,
        )


def downgrade() -> None:
    with op.batch_alter_table("users") as batch:
        batch.alter_column(
            "invite_token",
            existing_type=sa.String(length=64),
            type_=sa.String(length=32),
            existing_nullable=True,
        )
        batch.drop_column("invite_issued_at")
        batch.drop_column("invite_expires_at")
