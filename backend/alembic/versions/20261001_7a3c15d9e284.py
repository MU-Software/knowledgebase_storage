"""raw storage, git networks, prompt versions, project scopes and links

Revision ID: 7a3c15d9e284
Revises: b7e4a16c8f52
Create Date: 2026-10-01 13:30:00.000000+09:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "7a3c15d9e284"
down_revision: str | None = "b7e4a16c8f52"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UPLOAD_KIND = sa.Enum("transcript", "git_bundle", "worktree", name="uploadkind", native_enum=False, create_constraint=True)
PROMPT_STAGE = sa.Enum(
    "session_explore",
    "session_chunk",
    "session_final",
    "session_verify",
    "session_split",
    "project_overview",
    "project_link",
    "memory_merge",
    "note_relations",
    name="promptstage",
    native_enum=False,
    create_constraint=True,
)
PROMPT_STATUS = sa.Enum("draft", "active", "archived", name="promptstatus", native_enum=False, create_constraint=True)
LINK_KIND = sa.Enum("same", "part_of", "related", name="linkkind", native_enum=False, create_constraint=True)
NOTE_VERDICT = sa.Enum("hide", "purge", name="noteverdict", native_enum=False, create_constraint=True)
SUGGESTION_KIND = sa.Enum("merge", "nest", name="suggestionkind", native_enum=False, create_constraint=True)
JOB_KINDS = ("session", "memory_merge", "project_overview", "note_relations", "project_link")
OLD_JOB_KINDS = ("session", "memory_merge", "project_overview", "project_suggestion", "note_relations")
JOB_KIND = sa.Enum(*JOB_KINDS, name="jobkind", native_enum=False, create_constraint=False)
RUNTIME_SETTINGS = (
    ("display_timezone", sa.String(), "Asia/Seoul"),
    ("verify_max_claims", sa.Integer(), "12"),
    ("rebuild_batch_size", sa.Integer(), "0"),
    ("rebuild_max_age_days", sa.Integer(), "30"),
)
OLD_JOB_KIND = sa.Enum(*OLD_JOB_KINDS, name="jobkind", native_enum=False, create_constraint=False)


def listed(kinds: tuple[str, ...]) -> str:
    return ", ".join(f"'{kind}'" for kind in kinds)


def jsonb() -> postgresql.JSONB:
    return postgresql.JSONB(none_as_null=True, astext_type=sa.Text())


def stamps() -> list[sa.Column]:
    return [
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    ]


def upgrade() -> None:
    op.create_table(
        "raw_transcript",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("agent", sa.String(), nullable=False),
        sa.Column("device", sa.String(), nullable=False),
        sa.Column("session_id", sa.String(), nullable=False),
        sa.Column("project_hint", sa.String(), nullable=False),
        sa.Column("cwd", sa.String(), nullable=True),
        sa.Column("byte_size", sa.BigInteger(), nullable=False),
        sa.Column("digest", sa.String(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        *stamps(),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("agent", "device", "session_id", name="uq_raw_transcript_session"),
    )

    op.create_table(
        "upload",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("kind", UPLOAD_KIND, nullable=False),
        sa.Column("byte_size", sa.BigInteger(), nullable=False),
        sa.Column("part_count", sa.Integer(), nullable=False),
        sa.Column("digest", sa.String(), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        *stamps(),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "git_network",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("roots", jsonb(), nullable=False),
        sa.Column("label", sa.String(), nullable=False),
        *stamps(),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "git_source",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("network_id", sa.Uuid(), nullable=False),
        sa.Column("device", sa.String(), nullable=False),
        sa.Column("path", sa.String(), nullable=False),
        sa.Column("remote", sa.String(), nullable=True),
        sa.Column("refs", jsonb(), nullable=False),
        sa.Column("snapshot_ref", sa.String(), nullable=True),
        *stamps(),
        sa.ForeignKeyConstraint(["network_id"], ["git_network.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("device", "path", name="uq_git_source_location"),
    )
    op.create_index(op.f("ix_git_source_network_id"), "git_source", ["network_id"], unique=False)

    op.create_table(
        "prompt",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("stage", PROMPT_STAGE, nullable=False),
        sa.Column("status", PROMPT_STATUS, nullable=False),
        sa.Column("label", sa.String(), nullable=False),
        sa.Column("system", sa.Text(), nullable=False),
        sa.Column("instruction", sa.Text(), nullable=False),
        sa.Column("thinking", sa.Boolean(), nullable=False),
        sa.Column("temperature", sa.Float(), nullable=False),
        sa.Column("max_tokens", sa.Integer(), nullable=False),
        sa.Column("parent_id", sa.Uuid(), nullable=True),
        *stamps(),
        sa.ForeignKeyConstraint(["parent_id"], ["prompt.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_prompt_stage_status", "prompt", ["stage", "status"], unique=False)

    op.create_table(
        "project_entry",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("slug", sa.String(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("sources", jsonb(), nullable=False),
        sa.Column("container", sa.Boolean(), nullable=False),
        *stamps(),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_project_entry_slug"), "project_entry", ["slug"], unique=True)

    op.create_table(
        "note_decision",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("agent", sa.String(), nullable=False),
        sa.Column("device", sa.String(), nullable=False),
        sa.Column("session_id", sa.String(), nullable=False),
        sa.Column("verdict", NOTE_VERDICT, nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        *stamps(),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("agent", "device", "session_id", name="uq_note_decision"),
    )
    op.create_index(op.f("ix_note_decision_session_id"), "note_decision", ["session_id"], unique=False)

    op.create_table(
        "note_pin",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("agent", sa.String(), nullable=False),
        sa.Column("device", sa.String(), nullable=False),
        sa.Column("session_id", sa.String(), nullable=False),
        sa.Column("project", sa.String(), nullable=False),
        sa.Column("first_request", sa.Integer(), nullable=False),
        sa.Column("last_request", sa.Integer(), nullable=False),
        *stamps(),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("agent", "device", "session_id", "first_request", name="uq_note_pin"),
    )
    op.create_index(op.f("ix_note_pin_session_id"), "note_pin", ["session_id"], unique=False)
    op.create_index(op.f("ix_note_pin_project"), "note_pin", ["project"], unique=False)

    op.create_table(
        "project_link",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("kind", LINK_KIND, nullable=False),
        sa.Column("source", sa.String(), nullable=False),
        sa.Column("target", sa.String(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("stated_by_user", sa.Text(), nullable=False),
        sa.Column("confirmed", sa.Boolean(), nullable=False),
        sa.Column("unrelated", sa.Boolean(), nullable=False),
        sa.Column("summarizer", sa.String(), nullable=False),
        *stamps(),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("source", "target", name="uq_project_link_pair"),
    )
    op.create_index(op.f("ix_project_link_source"), "project_link", ["source"], unique=False)
    op.create_index(op.f("ix_project_link_target"), "project_link", ["target"], unique=False)

    op.drop_index(op.f("ix_project_suggestion_source"), table_name="project_suggestion")
    op.drop_table("project_suggestion")
    op.execute("DELETE FROM job WHERE kind = 'project_suggestion'")
    for column, kind, default in RUNTIME_SETTINGS:
        op.add_column("runtime_setting", sa.Column(column, kind, server_default=default, nullable=False))
        op.alter_column("runtime_setting", column, server_default=None)

    op.execute("ALTER TABLE job DROP CONSTRAINT IF EXISTS jobkind")
    op.alter_column("job", "kind", type_=JOB_KIND, existing_type=OLD_JOB_KIND, existing_nullable=False)
    op.execute(f"ALTER TABLE job ADD CONSTRAINT jobkind CHECK (kind IN ({listed(JOB_KINDS)}))")


def downgrade() -> None:
    op.execute("DELETE FROM job WHERE kind = 'project_link'")
    op.execute("ALTER TABLE job DROP CONSTRAINT IF EXISTS jobkind")
    op.alter_column("job", "kind", type_=OLD_JOB_KIND, existing_type=JOB_KIND, existing_nullable=False)
    op.execute(f"ALTER TABLE job ADD CONSTRAINT jobkind CHECK (kind IN ({listed(OLD_JOB_KINDS)}))")

    op.create_table(
        "project_suggestion",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("kind", SUGGESTION_KIND, nullable=False),
        sa.Column("source", sa.String(), nullable=False),
        sa.Column("target", sa.String(), nullable=False),
        sa.Column("reason", sa.String(), nullable=False),
        sa.Column("summarizer", sa.String(), nullable=False),
        sa.Column("applied_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("dismissed_at", sa.DateTime(timezone=True), nullable=True),
        *stamps(),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("kind", "source", "target", name="uq_project_suggestion"),
    )
    op.create_index(op.f("ix_project_suggestion_source"), "project_suggestion", ["source"], unique=False)

    for column in ("display_timezone", "verify_max_claims", "rebuild_batch_size", "rebuild_max_age_days"):
        op.drop_column("runtime_setting", column)

    op.drop_index(op.f("ix_note_pin_project"), table_name="note_pin")
    op.drop_index(op.f("ix_note_pin_session_id"), table_name="note_pin")
    op.drop_table("note_pin")
    op.drop_index(op.f("ix_note_decision_session_id"), table_name="note_decision")
    op.drop_table("note_decision")
    op.drop_index(op.f("ix_project_link_target"), table_name="project_link")
    op.drop_index(op.f("ix_project_link_source"), table_name="project_link")
    op.drop_table("project_link")
    op.drop_index(op.f("ix_project_entry_slug"), table_name="project_entry")
    op.drop_table("project_entry")
    op.drop_index("ix_prompt_stage_status", table_name="prompt")
    op.drop_table("prompt")
    op.drop_index(op.f("ix_git_source_network_id"), table_name="git_source")
    op.drop_table("git_source")
    op.drop_table("git_network")
    op.drop_table("upload")
    op.drop_table("raw_transcript")
