"""project hierarchy, merge aliases and memory-merge jobs

Revision ID: 8c31d5be40a7
Revises: 4f2c8ab15d93
Create Date: 2026-09-20 20:00:00.000000+09:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "8c31d5be40a7"
down_revision: str | None = "4f2c8ab15d93"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JOB_KIND = sa.Enum("session", "memory_merge", name="jobkind", native_enum=False, create_constraint=True)


def upgrade() -> None:
    op.add_column("job", sa.Column("kind", JOB_KIND, server_default="session", nullable=False))
    op.alter_column("job", "kind", server_default=None)

    op.create_table(
        "project_alias",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("source", sa.String(), nullable=False),
        sa.Column("slug", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_project_alias_source"), "project_alias", ["source"], unique=True)
    op.create_index(op.f("ix_project_alias_slug"), "project_alias", ["slug"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_project_alias_slug"), table_name="project_alias")
    op.drop_index(op.f("ix_project_alias_source"), table_name="project_alias")
    op.drop_table("project_alias")

    op.drop_constraint("jobkind", "job", type_="check")
    op.drop_column("job", "kind")
