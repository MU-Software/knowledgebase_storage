"""job heartbeat

Revision ID: 4e9b2d7a1c63
Revises: 7a3c15d9e284
Create Date: 2026-10-02 17:30:00.000000+09:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "4e9b2d7a1c63"
down_revision: str | None = "7a3c15d9e284"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("job", sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("job", "heartbeat_at")
