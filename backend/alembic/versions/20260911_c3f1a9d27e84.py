"""keep the last request of each job

Revision ID: c3f1a9d27e84
Revises: 5b8e2d6f1c47
Create Date: 2026-09-11 15:00:00.000000+09:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
import sqlmodel.sql.sqltypes
from alembic import op

revision: str = "c3f1a9d27e84"
down_revision: str | None = "5b8e2d6f1c47"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("job", sa.Column("last_request", sqlmodel.sql.sqltypes.AutoString(), nullable=True))
    op.execute(
        """
        UPDATE job
        SET last_request = left(jsonb_path_query_array(transcript, '$[*] ? (@.role == "user").content') ->> -1, 1000)
        WHERE transcript IS NOT NULL
        """
    )


def downgrade() -> None:
    op.drop_column("job", "last_request")
