"""users and api keys

Revision ID: 7e2b9c4d1a36
Revises: c3f1a9d27e84
Create Date: 2026-09-11 18:00:00.000000+09:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
import sqlmodel.sql.sqltypes
from alembic import op

revision: str = "7e2b9c4d1a36"
down_revision: str | None = "c3f1a9d27e84"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


TIMESTAMPED_TABLES = ("user", "api_key")


def upgrade() -> None:
    op.create_table(
        "user",
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("username", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("password", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("password_updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("uq_user_username", "user", ["username"], unique=True, postgresql_where=sa.text("deleted_at IS NULL"))
    op.create_table(
        "api_key",
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("name", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("prefix", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("key_digest", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["user.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_api_key_key_digest"), "api_key", ["key_digest"], unique=True)
    op.create_index(op.f("ix_api_key_user_id"), "api_key", ["user_id"], unique=False)

    op.add_column("runtime_setting", sa.Column("session_ttl_hours", sa.Integer(), server_default="168", nullable=False))
    op.alter_column("runtime_setting", "session_ttl_hours", server_default=None)

    for table in TIMESTAMPED_TABLES:
        op.execute(
            f"""
            CREATE TRIGGER trg_set_updated_at BEFORE UPDATE ON "{table}"
            FOR EACH ROW EXECUTE FUNCTION set_updated_at();
            """
        )


def downgrade() -> None:
    for table in TIMESTAMPED_TABLES:
        op.execute(f'DROP TRIGGER IF EXISTS trg_set_updated_at ON "{table}";')

    op.drop_column("runtime_setting", "session_ttl_hours")
    op.drop_index(op.f("ix_api_key_user_id"), table_name="api_key")
    op.drop_index(op.f("ix_api_key_key_digest"), table_name="api_key")
    op.drop_table("api_key")
    op.drop_index("uq_user_username", table_name="user")
    op.drop_table("user")
