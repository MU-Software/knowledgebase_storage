"""unique job per session

Revision ID: a1c4e7b93f20
Revises: 0d74d36c9be5
Create Date: 2026-09-09 17:40:00.000000+09:00
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
from sqlalchemy.dialects import postgresql  # noqa: F401

revision: str = "a1c4e7b93f20"
down_revision: str | None = "0d74d36c9be5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_unique_constraint("uq_job_session", "job", ["agent", "device", "session_id"])


def downgrade() -> None:
    op.drop_constraint("uq_job_session", "job", type_="unique")
