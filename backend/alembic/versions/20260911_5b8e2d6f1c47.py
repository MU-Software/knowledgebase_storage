"""refresh jobs with newer session snapshots

Revision ID: 5b8e2d6f1c47
Revises: a1c4e7b93f20
Create Date: 2026-09-11 12:00:00.000000+09:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
import sqlmodel.sql.sqltypes
from alembic import op

revision: str = "5b8e2d6f1c47"
down_revision: str | None = "a1c4e7b93f20"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("job", sa.Column("last_activity_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False))
    op.add_column("job", sa.Column("transcript_digest", sqlmodel.sql.sqltypes.AutoString(), nullable=True))
    op.execute("UPDATE job SET last_activity_at = created_at")

    op.add_column("runtime_setting", sa.Column("job_idle_seconds", sa.Integer(), server_default="600", nullable=False))
    op.alter_column("runtime_setting", "job_idle_seconds", server_default=None)


def downgrade() -> None:
    op.drop_column("runtime_setting", "job_idle_seconds")
    op.drop_column("job", "transcript_digest")
    op.drop_column("job", "last_activity_at")
