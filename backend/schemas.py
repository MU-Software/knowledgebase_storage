from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Annotated, Any, Literal, Self
from uuid import UUID

import frontmatter
from pydantic import BaseModel, Field

from backend.consts.notes import UNFILED, project_segments
from backend.models import MIN_CONTEXT_TOKENS, JobBase, JobKind, JobStatus, LLMProviderBase, ProviderKind, RuntimeSettingBase, SuggestionKind

if TYPE_CHECKING:
    from pathlib import Path

ObservationCategory = Literal["decision", "problem", "next", "fact", "idea"]
MemoryFileName = Annotated[str, Field(pattern=r"^[^/\\]+\.md$")]
ProjectName = Annotated[str, Field(min_length=1, max_length=200)]


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


class SuggestedLink(BaseModel):
    kind: SuggestionKind = Field(description="merge when the two are the same work, nest when one belongs under the other")
    source: str = Field(description="name of the project that is absorbed or moves")
    target: str = Field(description="name of the project it joins")
    reason: str = Field(description="One sentence on what makes them the same work.")


class ProjectSuggestions(BaseModel):
    suggestions: list[SuggestedLink] = Field(default_factory=list)


class NoteRelations(BaseModel):
    relations: list[Relation] = Field(default_factory=list)


class SuggestionPublic(BaseModel):
    id: UUID
    kind: SuggestionKind
    source: str
    target: str
    reason: str
    summarizer: str
    created_at: datetime


class ProjectOverview(SummaryResult):
    title: str = Field(description="The name of the project, not the title of a session.")
    summary: str = Field(description="Two to five sentences on what the project is and where it stands now.")
    observations: list[Observation] = Field(default_factory=list, description="What holds, what is open and what is queued for this project.")


class JobFailRequest(BaseModel):
    error: str


class JobPublic(JobBase):
    id: UUID
    kind: JobKind
    status: JobStatus
    created_at: datetime
    last_activity_at: datetime
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
    project_path: str
    tags: list[str] = Field(default_factory=list)
    source: dict[str, Any] = Field(default_factory=dict)

    @staticmethod
    def parse_markdown_file(path: Path, root: Path) -> tuple[dict[str, Any], str]:
        post = frontmatter.load(path)
        source = post.metadata.get("source")
        tags = post.metadata.get("tags")
        relative = path.relative_to(root).as_posix()
        segments = project_segments(relative)
        fields = {
            "path": relative,
            "title": str(post.metadata.get("title") or path.stem),
            "project": segments[-1] if segments else UNFILED,
            "project_path": "/".join(segments) if segments else UNFILED,
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


class ProjectNode(BaseModel):
    path: str = Field(description="where the project sits under the notes directory, e.g. pyconkr/pyconkr-backend")
    name: str
    parent: str | None
    depth: int
    note_count: int = Field(description="notes filed under this project itself")
    total_note_count: int = Field(description="notes filed under this project and everything below it")
    child_count: int
    unconfirmed: bool
    aliases: list[str] = Field(default_factory=list, description="inferred names a merge redirected here")
    overview: str | None = Field(default=None, description="path of the note that summarizes the project, once one has been written")


class ProjectMerge(BaseModel):
    source: ProjectName
    target: ProjectName


class ProjectMergeResult(BaseModel):
    project: str
    moved: int = Field(description="notes that changed hands")
    archived: list[str] = Field(default_factory=list, description="colliding memory files kept under archive/")
    merging: list[str] = Field(default_factory=list, description="memory files an LLM is merging in the background")


class ProjectReparent(BaseModel):
    parent: ProjectName | None = Field(default=None, description="the project to file this one under, or null for the top level")


class ProjectDeleteResult(BaseModel):
    path: str
    deleted_notes: int
    deleted_projects: list[str]


class MemoryContent(BaseModel):
    content: str


class JobClaimed(BaseModel):
    id: UUID
    kind: JobKind
    priority: int
    agent: str
    device: str
    project: str
    note_path: str | None
    summarizer: str | None
    refined_at: datetime | None
    last_activity_at: datetime
    claim_token: UUID | None
    transcript: list[dict[str, Any]] | None
    context: str = Field(default="", description="notes from the projects above this one, as background for the summary")
    targets: list[str] = Field(default_factory=list, description="the names this job works with: child projects, or the notes it may link to")


class RuntimeSettingUpdate(BaseModel):
    document_language: str | None = None
    job_max_attempts: int | None = Field(default=None, ge=1)
    job_batch_size: int | None = Field(default=None, ge=1)
    job_idle_seconds: int | None = Field(default=None, ge=0)
    transcript_retention_hours: int | None = Field(default=None, ge=1)
    stale_claim_hours: int | None = Field(default=None, ge=1)
    maintenance_interval_seconds: int | None = Field(default=None, ge=5)
    worker_poll_interval_seconds: int | None = Field(default=None, ge=1)
    session_ttl_hours: int | None = Field(default=None, ge=1)
    login_failure_window_minutes: int | None = Field(default=None, ge=1)
    login_max_failures_per_ip: int | None = Field(default=None, ge=1)
    login_max_failures_per_username: int | None = Field(default=None, ge=1)
    overview_min_new_logs: int | None = Field(default=None, ge=1)
    overview_max_age_days: int | None = Field(default=None, ge=1)
    background_sweep_hours: int | None = Field(default=None, ge=1)
    background_batch_size: int | None = Field(default=None, ge=1)


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
    max_concurrency: int | None = Field(default=None, ge=1)
    background_jobs: bool | None = None
    connect_timeout_seconds: float | None = Field(default=None, gt=0)
    timeout_seconds: float | None = Field(default=None, gt=0)
    enabled: bool | None = None


class LLMProviderPublic(LLMProviderBase):
    id: UUID
    updated_at: datetime
    has_api_key: bool


class LLMProviderResolved(LLMProviderPublic):
    api_key: str


class ProviderTestResult(BaseModel):
    ok: bool
    latency_ms: int
    detail: str = Field(default="", description="the note title the provider produced, or the error that stopped it")


class WorkerConfig(BaseModel):
    document_language: str
    poll_interval_seconds: int
    providers: list[LLMProviderResolved]


class NoteWrite(BaseModel):
    path: str = Field(description="path under the notes directory, e.g. projects/foo/memory/bar.md")
    content: str


class MemoryFile(BaseModel):
    name: MemoryFileName
    content: str


class MemorySync(BaseModel):
    agent: str
    device: str
    project: str
    files: list[MemoryFile] = Field(default_factory=list)
    deleted: list[MemoryFileName] = Field(default_factory=list)


class MemorySyncResult(BaseModel):
    written: list[str]
    deleted: list[str]


class ProjectContext(BaseModel):
    context: str


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=255)
    password: str = Field(min_length=1)


class AccessTokenResponse(BaseModel):
    access_token: str
    token_type: Literal["bearer"] = "bearer"  # noqa: S105


class UserPublic(BaseModel):
    id: UUID
    username: str
    last_login_at: datetime | None


class APIKeyCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    expires_in_days: int | None = Field(default=None, ge=1)


class APIKeyPublic(BaseModel):
    id: UUID
    name: str
    prefix: str
    created_at: datetime
    deleted_at: datetime | None
    last_used_at: datetime | None


class APIKeyCreated(APIKeyPublic):
    key: str
