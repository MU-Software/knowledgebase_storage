from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import DateTime, Enum, Index, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.sql.functions import now
from sqlmodel import Field, SQLModel

SINGLETON_ID = 1
PROMPT_OVERHEAD_TOKENS = 4096
MIN_BUDGET_TOKENS = 2048
MIN_CONTEXT_TOKENS = PROMPT_OVERHEAD_TOKENS + MIN_BUDGET_TOKENS


class JobStatus(StrEnum):
    PENDING = "pending"
    CLAIMED = "claimed"
    DONE = "done"
    FAILED = "failed"


class ProviderKind(StrEnum):
    LLAMA = "llama"
    ANTHROPIC = "anthropic"


class ProjectInference(StrEnum):
    REMOTE = "remote"
    WORKTREE = "worktree"
    CWD = "cwd"
    SCRATCH = "scratch"


def enum_type(enum_class: type[StrEnum]) -> Enum:
    return Enum(
        enum_class,
        native_enum=False,
        create_constraint=True,
        values_callable=lambda members: [member.value for member in members],
    )


class UUIDMixin(SQLModel):
    id: UUID = Field(default_factory=uuid4, primary_key=True)


class TimestampMixin(SQLModel):
    created_at: datetime = Field(sa_type=DateTime(timezone=True), sa_column_kwargs={"server_default": text("now()")})
    updated_at: datetime = Field(
        sa_type=DateTime(timezone=True),
        sa_column_kwargs={"server_default": text("now()"), "onupdate": now()},
    )


class JobBase(SQLModel):
    agent: str
    model: str | None = None
    device: str
    session_id: str

    project: str
    project_inference: ProjectInference = Field(sa_type=enum_type(ProjectInference))
    cwd: str | None = None

    transcript: list[dict[str, Any]] | None = Field(default=None, sa_type=JSONB)
    started_at: datetime | None = Field(default=None, sa_type=DateTime(timezone=True))
    ended_at: datetime | None = Field(default=None, sa_type=DateTime(timezone=True))


class Job(UUIDMixin, TimestampMixin, JobBase, table=True):
    __tablename__ = "job"
    __table_args__ = (
        Index("ix_job_pending_created", "status", "created_at"),
        UniqueConstraint("agent", "device", "session_id", name="uq_job_session"),
    )

    status: JobStatus = Field(default=JobStatus.PENDING, sa_type=enum_type(JobStatus))

    claimed_at: datetime | None = Field(default=None, sa_type=DateTime(timezone=True))
    claimed_by: str | None = None
    claim_token: UUID | None = None
    next_attempt_at: datetime | None = Field(default=None, sa_type=DateTime(timezone=True))
    attempts: int = 0
    last_error: str | None = None

    summarizer: str | None = None
    note_path: str | None = None
    completed_at: datetime | None = Field(default=None, sa_type=DateTime(timezone=True))


class RuntimeSettingBase(SQLModel):
    document_language: str = "Korean"
    job_max_attempts: int = Field(default=3, ge=1)
    job_batch_size: int = Field(default=5, ge=1)
    transcript_retention_hours: int = Field(default=24, ge=1)
    stale_claim_hours: int = Field(default=1, ge=1)
    maintenance_interval_seconds: int = Field(default=60, ge=5)
    worker_poll_interval_seconds: int = Field(default=15, ge=1)


class RuntimeSetting(TimestampMixin, RuntimeSettingBase, table=True):
    __tablename__ = "runtime_setting"

    id: int = Field(default=SINGLETON_ID, primary_key=True)


class LLMProviderBase(SQLModel):
    name: str = Field(unique=True, index=True)
    kind: ProviderKind = Field(sa_type=enum_type(ProviderKind))
    model: str
    base_url: str | None = None
    context_tokens: int = Field(default=32768, ge=MIN_CONTEXT_TOKENS)
    priority: int = Field(default=100, ge=0)
    min_job_age_seconds: int = Field(default=0, ge=0)
    connect_timeout_seconds: float = Field(default=5.0, gt=0)
    timeout_seconds: float = Field(default=600.0, gt=0)
    enabled: bool = True


class LLMProvider(UUIDMixin, TimestampMixin, LLMProviderBase, table=True):
    __tablename__ = "llm_provider"

    api_key: str = ""
