"""blobs and worktree files

Revision ID: 6c1d8e3f9a52
Revises: 4e9b2d7a1c63
Create Date: 2026-10-03 10:00:00.000000+09:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "6c1d8e3f9a52"
down_revision: str | None = "4e9b2d7a1c63"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "blob",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("digest", sa.String(), nullable=False),
        sa.Column("byte_size", sa.BigInteger(), nullable=False),
        sa.Column("chunks", postgresql.JSONB(none_as_null=True, astext_type=sa.Text()), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_blob_digest"), "blob", ["digest"], unique=True)
    op.create_table(
        "worktree_file",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("source_id", sa.Uuid(), nullable=False),
        sa.Column("path", sa.String(), nullable=False),
        sa.Column("digest", sa.String(), nullable=False),
        sa.Column("byte_size", sa.BigInteger(), nullable=False),
        sa.Column("modified_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["source_id"], ["git_source.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("source_id", "path", name="uq_worktree_file_location"),
    )
    op.create_index(op.f("ix_worktree_file_source_id"), "worktree_file", ["source_id"], unique=False)
    op.create_index(op.f("ix_worktree_file_digest"), "worktree_file", ["digest"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_worktree_file_digest"), table_name="worktree_file")
    op.drop_index(op.f("ix_worktree_file_source_id"), table_name="worktree_file")
    op.drop_table("worktree_file")
    op.drop_index(op.f("ix_blob_digest"), table_name="blob")
    op.drop_table("blob")
