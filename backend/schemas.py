from __future__ import annotations

from datetime import datetime  # noqa: TC003
from typing import TYPE_CHECKING, Any, Literal, Self
from uuid import UUID  # noqa: TC003

import frontmatter
from pydantic import BaseModel, Field

from backend.models import MIN_CONTEXT_TOKENS, JobBase, JobStatus, LLMProviderBase, ProviderKind, RuntimeSettingBase

if TYPE_CHECKING:
    from pathlib import Path

ObservationCategory = Literal["decision", "problem", "next", "fact", "idea"]

PROJECT_PATH_SEGMENTS = 2


class Observation(BaseModel):
    category: ObservationCategory
    text: str
    tags: list[str] = Field(default_factory=list)


class Relation(BaseModel):
    type: str = Field(default="relates_to")
    target: str


class SummaryResult(BaseModel):
    title: str
    summary: str = Field(description="Two to five sentences on what this session did.")
    observations: list[Observation] = Field(default_factory=list)
    relations: list[Relation] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)


class JobFailRequest(BaseModel):
    error: str


class JobPublic(JobBase):
    id: UUID
    status: JobStatus
    created_at: datetime
    claimed_at: datetime | None
    claimed_by: str | None
    attempts: int
    last_error: str | None
    summarizer: str | None
    note_path: str | None
    completed_at: datetime | None

    transcript: list[dict[str, Any]] | None = Field(default=None, exclude=True)


class NoteSummary(BaseModel):
    path: str
    title: str
    project: str
    tags: list[str] = Field(default_factory=list)
    source: dict[str, Any] = Field(default_factory=dict)

    @staticmethod
    def parse_markdown_file(path: Path, root: Path) -> tuple[dict[str, Any], str]:
        post = frontmatter.load(path)
        source = post.metadata.get("source")
        tags = post.metadata.get("tags")
        relative = path.relative_to(root).as_posix()
        parts = relative.split("/")
        fields = {
            "path": relative,
            "title": str(post.metadata.get("title") or path.stem),
            "project": parts[1] if len(parts) > PROJECT_PATH_SEGMENTS and parts[0] == "projects" else "_unfiled",
            "tags": [str(tag) for tag in tags] if isinstance(tags, list) else [],
            "source": source if isinstance(source, dict) else {},
        }
        return fields, post.content

    @classmethod
    def from_markdown_file(cls, path: Path, root: Path) -> Self:
        fields, _ = cls.parse_markdown_file(path, root)
        return cls(**fields)


class NoteDetail(NoteSummary):
    content: str

    @classmethod
    def from_markdown_file(cls, path: Path, root: Path) -> Self:
        fields, content = cls.parse_markdown_file(path, root)
        return cls(**fields, content=content)


class ProjectSummary(BaseModel):
    name: str
    note_count: int
    unconfirmed: bool


class RuntimeSettingUpdate(BaseModel):
    document_language: str | None = None
    job_max_attempts: int | None = Field(default=None, ge=1)
    job_batch_size: int | None = Field(default=None, ge=1)
    transcript_retention_hours: int | None = Field(default=None, ge=1)
    stale_claim_hours: int | None = Field(default=None, ge=1)
    maintenance_interval_seconds: int | None = Field(default=None, ge=5)
    worker_poll_interval_seconds: int | None = Field(default=None, ge=1)


class RuntimeSettingPublic(RuntimeSettingBase):
    updated_at: datetime


class LLMProviderCreate(LLMProviderBase):
    api_key: str = ""


class LLMProviderUpdate(BaseModel):
    name: str | None = None
    kind: ProviderKind | None = None
    model: str | None = None
    base_url: str | None = None
    api_key: str | None = None
    context_tokens: int | None = Field(default=None, ge=MIN_CONTEXT_TOKENS)
    priority: int | None = Field(default=None, ge=0)
    min_job_age_seconds: int | None = Field(default=None, ge=0)
    connect_timeout_seconds: float | None = Field(default=None, gt=0)
    timeout_seconds: float | None = Field(default=None, gt=0)
    enabled: bool | None = None


class LLMProviderPublic(LLMProviderBase):
    id: UUID
    updated_at: datetime
    has_api_key: bool


class LLMProviderResolved(LLMProviderPublic):
    api_key: str


class WorkerConfig(BaseModel):
    document_language: str
    poll_interval_seconds: int
    providers: list[LLMProviderResolved]


class NoteWrite(BaseModel):
    path: str = Field(description="path under the notes directory, e.g. projects/foo/memory/bar.md")
    content: str
