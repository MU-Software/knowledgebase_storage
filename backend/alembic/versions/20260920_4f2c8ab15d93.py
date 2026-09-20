"""per-provider worker concurrency

Revision ID: 4f2c8ab15d93
Revises: 9d4a6e1f3b58
Create Date: 2026-09-20 18:00:00.000000+09:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "4f2c8ab15d93"
down_revision: str | None = "9d4a6e1f3b58"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("llm_provider", sa.Column("max_concurrency", sa.Integer(), server_default="1", nullable=False))
    op.alter_column("llm_provider", "max_concurrency", server_default=None)


def downgrade() -> None:
    op.drop_column("llm_provider", "max_concurrency")
