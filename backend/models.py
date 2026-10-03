from __future__ import annotations

import json
from datetime import UTC, datetime
from enum import StrEnum
from hashlib import sha256
from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from argon2 import PasswordHasher
from pydantic import field_validator
from sqlalchemy import BigInteger, DateTime, Enum, Index, Text, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.sql.functions import now
from sqlmodel import Field, SQLModel, col, or_
from sqlmodel.main import SQLModelConfig

from backend.utils.pydanticlib import PasswordField, UsernameField

if TYPE_CHECKING:
    from sqlalchemy.sql.elements import ColumnElement

SINGLETON_ID = 1
PROMPT_OVERHEAD_TOKENS = 4096
MIN_BUDGET_TOKENS = 2048
MIN_CONTEXT_TOKENS = PROMPT_OVERHEAD_TOKENS + MIN_BUDGET_TOKENS
API_KEY_PREFIX = "kbs_"
BACKGROUND_PRIORITY = 10
BACKGROUND_AGENT = "kbstore"
BACKGROUND_DEVICE = "server"
PASSWORD_HASHER = PasswordHasher()


class JobStatus(StrEnum):
    PENDING = "pending"
    CLAIMED = "claimed"
    DONE = "done"
    FAILED = "failed"


class JobKind(StrEnum):
    SESSION = "session"
    MEMORY_MERGE = "memory_merge"
    PROJECT_OVERVIEW = "project_overview"
    NOTE_RELATIONS = "note_relations"
    PROJECT_LINK = "project_link"


class ProviderKind(StrEnum):
    LLAMA = "llama"
    ANTHROPIC = "anthropic"


class ProjectInference(StrEnum):
    REMOTE = "remote"
    WORKTREE = "worktree"
    CWD = "cwd"
    SCRATCH = "scratch"


def slugify_segment(value: str) -> str:
    kept = "".join(char if char.isalnum() or char in "-_." else "-" for char in value.strip("/"))
    return kept.strip("-").lower() or "unknown"


def background_session(kind: JobKind, target: str = "") -> str:
    return f"{kind.value}:{target}"


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


class SoftDeleteMixin(SQLModel):
    deleted_at: datetime | None = Field(default=None, sa_type=DateTime(timezone=True))

    @classmethod
    def not_deleted(cls) -> ColumnElement[bool]:
        return or_(col(cls.deleted_at).is_(None), col(cls.deleted_at) > now())

    @property
    def is_deleted(self) -> bool:
        return self.deleted_at is not None and self.deleted_at <= datetime.now(UTC)


class JobBase(SQLModel):
    agent: str
    model: str | None = None
    device: str
    session_id: str

    project: str
    project_inference: ProjectInference = Field(sa_type=enum_type(ProjectInference))
    cwd: str | None = None

    transcript: list[dict[str, Any]] | None = Field(default=None, sa_type=JSONB(none_as_null=True))
    started_at: datetime | None = Field(default=None, sa_type=DateTime(timezone=True))
    ended_at: datetime | None = Field(default=None, sa_type=DateTime(timezone=True))

    @staticmethod
    def digest_of(messages: list[dict[str, Any]] | None) -> str:
        return sha256(json.dumps(messages, ensure_ascii=False, sort_keys=True).encode()).hexdigest()

    @property
    def digest(self) -> str:
        return self.digest_of(self.transcript)


class Job(UUIDMixin, TimestampMixin, JobBase, table=True):
    __tablename__ = "job"
    __table_args__ = (
        Index("ix_job_pending_created", "status", "created_at"),
        UniqueConstraint("agent", "device", "session_id", name="uq_job_session"),
    )

    kind: JobKind = Field(default=JobKind.SESSION, sa_type=enum_type(JobKind))
    priority: int = 0
    status: JobStatus = Field(default=JobStatus.PENDING, sa_type=enum_type(JobStatus))
    last_activity_at: datetime = Field(sa_type=DateTime(timezone=True), sa_column_kwargs={"server_default": text("now()")})
    transcript_digest: str | None = None
    last_request: str | None = None

    claimed_at: datetime | None = Field(default=None, sa_type=DateTime(timezone=True))
    claimed_by: str | None = None
    claim_token: UUID | None = None
    heartbeat_at: datetime | None = Field(default=None, sa_type=DateTime(timezone=True))
    next_attempt_at: datetime | None = Field(default=None, sa_type=DateTime(timezone=True))
    attempts: int = 0
    last_error: str | None = None

    summarizer: str | None = None
    note_path: str | None = None
    completed_at: datetime | None = Field(default=None, sa_type=DateTime(timezone=True))
    refined_at: datetime | None = Field(default=None, sa_type=DateTime(timezone=True))


class PromptStage(StrEnum):
    SESSION_EXPLORE = "session_explore"
    SESSION_CHUNK = "session_chunk"
    SESSION_FINAL = "session_final"
    SESSION_VERIFY = "session_verify"
    SESSION_SPLIT = "session_split"
    PROJECT_OVERVIEW = "project_overview"
    PROJECT_LINK = "project_link"
    MEMORY_MERGE = "memory_merge"
    NOTE_RELATIONS = "note_relations"


class PromptStatus(StrEnum):
    DRAFT = "draft"
    ACTIVE = "active"
    ARCHIVED = "archived"


class PromptBase(SQLModel):
    stage: PromptStage = Field(sa_type=enum_type(PromptStage))
    label: str = ""
    system: str = Field(sa_type=Text)
    instruction: str = Field(default="", sa_type=Text)
    thinking: bool = True
    temperature: float = Field(default=0.2, ge=0.0, le=2.0)
    max_tokens: int = Field(default=20000, ge=256)


class Prompt(UUIDMixin, TimestampMixin, PromptBase, table=True):
    __tablename__ = "prompt"
    __table_args__ = (Index("ix_prompt_stage_status", "stage", "status"),)

    status: PromptStatus = Field(default=PromptStatus.DRAFT, sa_type=enum_type(PromptStatus))
    parent_id: UUID | None = Field(default=None, foreign_key="prompt.id", ondelete="SET NULL")


class UploadKind(StrEnum):
    TRANSCRIPT = "transcript"
    GIT_BUNDLE = "git_bundle"
    WORKTREE = "worktree"


class RawTranscript(UUIDMixin, TimestampMixin, table=True):
    __tablename__ = "raw_transcript"
    __table_args__ = (UniqueConstraint("agent", "device", "session_id", name="uq_raw_transcript_session"),)

    agent: str
    device: str
    session_id: str
    project_hint: str = ""
    cwd: str | None = None
    byte_size: int = Field(default=0, sa_type=BigInteger)
    digest: str = ""
    started_at: datetime | None = Field(default=None, sa_type=DateTime(timezone=True))
    ended_at: datetime | None = Field(default=None, sa_type=DateTime(timezone=True))

    @property
    def relative_path(self) -> str:
        return f"transcripts/{slugify_segment(self.device)}/{slugify_segment(self.agent)}/{slugify_segment(self.session_id)}.jsonl"


class Upload(UUIDMixin, TimestampMixin, table=True):
    __tablename__ = "upload"

    kind: UploadKind = Field(sa_type=enum_type(UploadKind))
    byte_size: int = Field(default=0, sa_type=BigInteger)
    part_count: int = 0
    digest: str | None = None
    completed_at: datetime | None = Field(default=None, sa_type=DateTime(timezone=True))

    @property
    def relative_path(self) -> str:
        return f"uploads/{self.id}.part"


class GitNetwork(UUIDMixin, TimestampMixin, table=True):
    __tablename__ = "git_network"

    roots: list[str] = Field(default_factory=list, sa_type=JSONB(none_as_null=True))
    label: str = ""

    @property
    def relative_path(self) -> str:
        return f"repos/{self.id}.git"


class GitSource(UUIDMixin, TimestampMixin, table=True):
    __tablename__ = "git_source"
    __table_args__ = (UniqueConstraint("device", "path", name="uq_git_source_location"),)

    network_id: UUID = Field(foreign_key="git_network.id", ondelete="CASCADE", index=True)
    device: str
    path: str
    remote: str | None = None
    refs: dict[str, str] = Field(default_factory=dict, sa_type=JSONB(none_as_null=True))
    snapshot_ref: str | None = None

    @property
    def namespace(self) -> str:
        return f"refs/sources/{slugify_segment(self.device)}-{slugify_segment(self.path)}"


class Blob(UUIDMixin, TimestampMixin, table=True):
    __tablename__ = "blob"

    digest: str = Field(unique=True, index=True)
    byte_size: int = Field(default=0, sa_type=BigInteger)
    chunks: list[str] = Field(default_factory=list, sa_type=JSONB(none_as_null=True))
    completed_at: datetime | None = Field(default=None, sa_type=DateTime(timezone=True))

    @staticmethod
    def path_of(digest: str) -> str:
        return f"blobs/{digest[:2]}/{digest}"

    @staticmethod
    def chunk_path_of(digest: str) -> str:
        return f"chunks/{digest[:2]}/{digest}"


class WorktreeFile(UUIDMixin, TimestampMixin, table=True):
    __tablename__ = "worktree_file"
    __table_args__ = (UniqueConstraint("source_id", "path", name="uq_worktree_file_location"),)

    source_id: UUID = Field(foreign_key="git_source.id", ondelete="CASCADE", index=True)
    path: str
    digest: str = Field(index=True)
    byte_size: int = Field(default=0, sa_type=BigInteger)
    modified_at: datetime = Field(sa_type=DateTime(timezone=True))


class ProjectEntry(UUIDMixin, TimestampMixin, table=True):
    __tablename__ = "project_entry"

    slug: str = Field(unique=True, index=True)
    description: str = Field(default="", sa_type=Text)
    sources: list[dict[str, str]] = Field(default_factory=list, sa_type=JSONB(none_as_null=True))
    container: bool = Field(default=False)

    @staticmethod
    def covers(root: str, path: str) -> bool:
        return bool(root) and (path == root or path.startswith(root.rstrip("/") + "/"))

    def prefix_for(self, path: str) -> str:
        for source in self.sources:
            if self.covers(str(source.get("path") or ""), path):
                return str(source.get("prefix") or "")
        return ""


class NoteVerdict(StrEnum):
    HIDE = "hide"
    PURGE = "purge"


class NoteDecision(UUIDMixin, TimestampMixin, table=True):
    __tablename__ = "note_decision"
    __table_args__ = (UniqueConstraint("agent", "device", "session_id", name="uq_note_decision"),)

    agent: str
    device: str
    session_id: str = Field(index=True)
    verdict: NoteVerdict = Field(sa_type=enum_type(NoteVerdict))
    reason: str = Field(default="", sa_type=Text)


class NotePin(UUIDMixin, TimestampMixin, table=True):
    __tablename__ = "note_pin"
    __table_args__ = (UniqueConstraint("agent", "device", "session_id", "first_request", name="uq_note_pin"),)

    agent: str
    device: str
    session_id: str = Field(index=True)
    project: str = Field(index=True)
    first_request: int = Field(default=0, ge=0)
    last_request: int = Field(default=0, ge=0)

    def holds(self, number: int) -> bool:
        if self.first_request == 0:
            return True
        return self.first_request <= number <= (self.last_request or self.first_request)


class ProjectAlias(UUIDMixin, TimestampMixin, table=True):
    __tablename__ = "project_alias"

    source: str = Field(unique=True, index=True)
    slug: str = Field(index=True)


class LinkKind(StrEnum):
    SAME = "same"
    PART_OF = "part_of"
    RELATED = "related"


class ProjectLink(UUIDMixin, TimestampMixin, table=True):
    __tablename__ = "project_link"
    __table_args__ = (UniqueConstraint("source", "target", name="uq_project_link_pair"),)

    kind: LinkKind = Field(sa_type=enum_type(LinkKind))
    source: str = Field(index=True)
    target: str = Field(index=True)
    description: str = Field(default="", sa_type=Text)
    stated_by_user: str = Field(default="", sa_type=Text)
    confirmed: bool = False
    unrelated: bool = False
    summarizer: str = ""


class RuntimeSettingBase(SQLModel):
    document_language: str = "Korean"
    job_max_attempts: int = Field(default=3, ge=1)
    job_batch_size: int = Field(default=5, ge=1)
    job_idle_seconds: int = Field(default=600, ge=0)
    transcript_retention_hours: int = Field(default=24, ge=1)
    stale_claim_hours: int = Field(default=1, ge=1)
    maintenance_interval_seconds: int = Field(default=60, ge=5)
    worker_poll_interval_seconds: int = Field(default=15, ge=1)
    session_ttl_hours: int = Field(default=168, ge=1)
    login_failure_window_minutes: int = Field(default=15, ge=1)
    login_max_failures_per_ip: int = Field(default=5, ge=1)
    login_max_failures_per_username: int = Field(default=20, ge=1)
    overview_min_new_logs: int = Field(default=5, ge=1)
    overview_max_age_days: int = Field(default=7, ge=1)
    background_sweep_hours: int = Field(default=24, ge=1)
    background_batch_size: int = Field(default=3, ge=1)
    display_timezone: str = Field(default="Asia/Seoul")
    verify_max_claims: int = Field(default=12, ge=0)
    rebuild_batch_size: int = Field(default=0, ge=0)
    rebuild_max_age_days: int = Field(default=30, ge=1)

    @field_validator("display_timezone")
    @classmethod
    def known_zone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError) as error:
            message = f"{value} is not a known time zone"
            raise ValueError(message) from error
        return value

    @property
    def zone(self) -> ZoneInfo:
        return ZoneInfo(self.display_timezone)


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
    max_concurrency: int = Field(default=1, ge=1)
    background_jobs: bool = True
    connect_timeout_seconds: float = Field(default=5.0, gt=0)
    timeout_seconds: float = Field(default=600.0, gt=0)
    enabled: bool = True


class LLMProvider(UUIDMixin, TimestampMixin, LLMProviderBase, table=True):
    __tablename__ = "llm_provider"

    api_key: str = ""


class User(UUIDMixin, TimestampMixin, SoftDeleteMixin, table=True):
    __tablename__ = "user"
    __table_args__ = (Index("uq_user_username", "username", unique=True, postgresql_where=text("deleted_at IS NULL")),)
    model_config = SQLModelConfig(validate_assignment=True)

    username: UsernameField
    password: PasswordField = Field(exclude=True)
    password_updated_at: datetime = Field(sa_type=DateTime(timezone=True), sa_column_kwargs={"server_default": text("now()")})
    last_login_at: datetime | None = Field(default=None, sa_type=DateTime(timezone=True))

    @field_validator("password", mode="after")
    @classmethod
    def hash_password(cls, password: str) -> str:
        return PASSWORD_HASHER.hash(password)

    def compare_password(self, password: str) -> bool:
        return PASSWORD_HASHER.verify(self.password, password)


class APIKey(UUIDMixin, TimestampMixin, SoftDeleteMixin, table=True):
    __tablename__ = "api_key"

    user_id: UUID = Field(foreign_key="user.id", ondelete="RESTRICT", index=True)
    name: str
    prefix: str
    key_digest: str = Field(unique=True, index=True)
    last_used_at: datetime | None = Field(default=None, sa_type=DateTime(timezone=True))


class LoginFailure(UUIDMixin, table=True):
    __tablename__ = "login_failure"

    username: str = Field(index=True)
    client_ip: str = Field(index=True)
    created_at: datetime = Field(sa_type=DateTime(timezone=True), sa_column_kwargs={"server_default": text("now()")}, index=True)
