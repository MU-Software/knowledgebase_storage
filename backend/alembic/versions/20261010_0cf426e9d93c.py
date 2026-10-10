"""job progress and checkpoint, blob chunks not null

Revision ID: 0cf426e9d93c
Revises: 6c1d8e3f9a52
Create Date: 2026-10-10 17:44:22.580832+09:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0cf426e9d93c"
down_revision: str | None = "6c1d8e3f9a52"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("UPDATE blob SET chunks = '[]'::jsonb WHERE chunks IS NULL")
    op.alter_column("blob", "chunks", existing_type=postgresql.JSONB(astext_type=sa.Text()), nullable=False)
    op.add_column("job", sa.Column("progress", sa.String(), nullable=True))
    op.add_column("job", sa.Column("checkpoint", postgresql.JSONB(none_as_null=True, astext_type=sa.Text()), nullable=True))


def downgrade() -> None:
    op.drop_column("job", "checkpoint")
    op.drop_column("job", "progress")
    op.alter_column("blob", "chunks", existing_type=postgresql.JSONB(astext_type=sa.Text()), nullable=True)
