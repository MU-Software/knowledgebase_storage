"""sign-in failures and throttling settings

Revision ID: 9d4a6e1f3b58
Revises: 7e2b9c4d1a36
Create Date: 2026-09-11 19:00:00.000000+09:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
import sqlmodel.sql.sqltypes
from alembic import op

revision: str = "9d4a6e1f3b58"
down_revision: str | None = "7e2b9c4d1a36"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


THROTTLE_SETTINGS = (
    ("login_failure_window_minutes", "15"),
    ("login_max_failures_per_ip", "5"),
    ("login_max_failures_per_username", "20"),
)
INDEXED_COLUMNS = ("username", "client_ip", "created_at")


def upgrade() -> None:
    op.create_table(
        "login_failure",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("username", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("client_ip", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    for column in INDEXED_COLUMNS:
        op.create_index(op.f(f"ix_login_failure_{column}"), "login_failure", [column], unique=False)

    for name, default in THROTTLE_SETTINGS:
        op.add_column("runtime_setting", sa.Column(name, sa.Integer(), server_default=default, nullable=False))
        op.alter_column("runtime_setting", name, server_default=None)


def downgrade() -> None:
    for name, _ in THROTTLE_SETTINGS:
        op.drop_column("runtime_setting", name)

    for column in INDEXED_COLUMNS:
        op.drop_index(op.f(f"ix_login_failure_{column}"), table_name="login_failure")
    op.drop_table("login_failure")
