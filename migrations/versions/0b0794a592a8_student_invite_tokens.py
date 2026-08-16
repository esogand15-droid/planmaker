"""student invite tokens

Lets an advisor create a student inside the bot and hand out a one-time deep
link (?start=inv_<token>) that binds the student's Telegram account to the
existing row — instead of a second, duplicate user being created on /start.

Revision ID: 0b0794a592a8
Revises: 946abecd3e6c
Create Date: 2026-08-16
"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0b0794a592a8"
down_revision: str | Sequence[str] | None = "946abecd3e6c"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # batch mode keeps this working on SQLite (dev/test) as well as PostgreSQL
    with op.batch_alter_table("users") as batch:
        batch.add_column(sa.Column("invite_token", sa.String(length=32), nullable=True))
        batch.add_column(sa.Column("created_by_id", sa.Integer(), nullable=True))
        batch.create_index("ix_users_invite_token", ["invite_token"], unique=True)
        batch.create_foreign_key(
            "fk_users_created_by_id_users",
            "users",
            ["created_by_id"],
            ["id"],
            ondelete="SET NULL",
        )


def downgrade() -> None:
    with op.batch_alter_table("users") as batch:
        batch.drop_constraint("fk_users_created_by_id_users", type_="foreignkey")
        batch.drop_index("ix_users_invite_token")
        batch.drop_column("created_by_id")
        batch.drop_column("invite_token")
