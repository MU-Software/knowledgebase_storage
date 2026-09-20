"""background work: project overviews, suggestions and relation sweeps

Revision ID: b7e4a16c8f52
Revises: 8c31d5be40a7
Create Date: 2026-09-20 21:30:00.000000+09:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b7e4a16c8f52"
down_revision: str | None = "8c31d5be40a7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JOB_KINDS = ("session", "memory_merge", "project_overview", "project_suggestion", "note_relations")
JOB_KIND = sa.Enum(*JOB_KINDS, name="jobkind", native_enum=False, create_constraint=False)
OVERVIEW_SETTINGS = (
    ("overview_min_new_logs", "5"),
    ("overview_max_age_days", "7"),
    ("background_sweep_hours", "24"),
    ("background_batch_size", "3"),
)


def check_kinds(kinds: tuple[str, ...]) -> str:
    return ", ".join(f"'{kind}'" for kind in kinds)


def upgrade() -> None:
    op.execute("UPDATE job SET transcript = NULL WHERE transcript = 'null'::jsonb")
    op.execute("ALTER TABLE job DROP CONSTRAINT IF EXISTS jobkind")
    op.alter_column("job", "kind", type_=JOB_KIND, existing_type=sa.String(length=12), existing_nullable=False)
    op.execute(f"ALTER TABLE job ADD CONSTRAINT jobkind CHECK (kind IN ({check_kinds(JOB_KINDS)}))")

    op.add_column("job", sa.Column("priority", sa.Integer(), server_default="0", nullable=False))
    op.alter_column("job", "priority", server_default=None)
    op.add_column("job", sa.Column("refined_at", sa.DateTime(timezone=True), nullable=True))

    op.add_column("llm_provider", sa.Column("background_jobs", sa.Boolean(), server_default=sa.true(), nullable=False))
    op.alter_column("llm_provider", "background_jobs", server_default=None)

    for column, default in OVERVIEW_SETTINGS:
        op.add_column("runtime_setting", sa.Column(column, sa.Integer(), server_default=default, nullable=False))
        op.alter_column("runtime_setting", column, server_default=None)

    op.create_table(
        "project_suggestion",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "kind",
            sa.Enum("merge", "nest", name="suggestionkind", native_enum=False, create_constraint=True),
            nullable=False,
        ),
        sa.Column("source", sa.String(), nullable=False),
        sa.Column("target", sa.String(), nullable=False),
        sa.Column("reason", sa.String(), nullable=False),
        sa.Column("summarizer", sa.String(), nullable=False),
        sa.Column("applied_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("dismissed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("kind", "source", "target", name="uq_project_suggestion"),
    )
    op.create_index(op.f("ix_project_suggestion_source"), "project_suggestion", ["source"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_project_suggestion_source"), table_name="project_suggestion")
    op.drop_table("project_suggestion")

    for column, _ in OVERVIEW_SETTINGS:
        op.drop_column("runtime_setting", column)
    op.drop_column("llm_provider", "background_jobs")
    op.drop_column("job", "refined_at")
    op.drop_column("job", "priority")

    op.execute("DELETE FROM job WHERE kind <> 'session' AND kind <> 'memory_merge'")
    op.execute("ALTER TABLE job DROP CONSTRAINT IF EXISTS jobkind")
    op.alter_column("job", "kind", type_=sa.String(length=12), existing_type=JOB_KIND, existing_nullable=False)
    op.execute(f"ALTER TABLE job ADD CONSTRAINT jobkind CHECK (kind IN ({check_kinds(JOB_KINDS[:2])}))")
