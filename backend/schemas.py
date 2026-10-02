from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Annotated, Any, Literal, Self
from uuid import UUID

import frontmatter
from pydantic import BaseModel, Field, field_validator

from backend.consts.notes import project_of
from backend.models import (
    MIN_CONTEXT_TOKENS,
    JobBase,
    JobKind,
    JobStatus,
    LinkKind,
    LLMProviderBase,
    NoteVerdict,
    PromptBase,
    PromptStatus,
    ProviderKind,
    RuntimeSettingBase,
    UploadKind,
)

if TYPE_CHECKING:
    from pathlib import Path

ObservationCategory = Literal["decision", "problem", "next", "change", "fact", "idea"]
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


class LinkCandidate(BaseModel):
    project: str = Field(description="a name copied exactly from the known projects list")
    relation: Literal["same", "part_of", "related"]
    description: str = Field(description="One sentence on how this session's project and that project connect.")
    quote: str = Field(description="The words in the log that state it, copied verbatim.")


class SessionItem(BaseModel):
    number: int = Field(description="the number of the request, from its '## 사용자 요청 #N' heading")
    topic: str = Field(description="A heading of at most six words naming what this request was about.")
    request: str = Field(description="What the user asked, in one short phrase.")
    outcome: str = Field(description="What came of it, in one short phrase.")
    observations: list[Observation] = Field(default_factory=list)
    links: list[LinkCandidate] = Field(default_factory=list)


class SessionChunk(BaseModel):
    items: list[SessionItem] = Field(default_factory=list, description="One item per heading, in order; never fold two requests into one.")


class Supersede(BaseModel):
    old: int
    by: int


class SessionHeader(BaseModel):
    title: str
    summary: str
    superseded: list[Supersede] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)


class SessionNote(BaseModel):
    header: SessionHeader
    items: list[SessionItem] = Field(default_factory=list)
    project: str = Field(default="", description="which project this note belongs to; empty means the job's own project")


class SessionNotes(BaseModel):
    notes: list[SessionNote] = Field(default_factory=list, description="one note per project the session worked on")


class VerifyVerdict(BaseModel):
    evidence: str
    verdict: Literal["confirmed", "partly", "not_found", "contradicted"]
    correction: str = Field(default="", description="the claim rewritten to match the repository, or empty when it already matches")


class SplitChoice(BaseModel):
    evidence: str
    project: str
    confidence: Literal["high", "low"]


class LinkVerdict(BaseModel):
    evidence: str = Field(description="Concrete facts from the two records: shared repository, paths, roots, what each one builds.")
    reasoning: str = Field(description="Weigh the evidence. Say what would have to be true for each verdict.")
    verdict: Literal["same", "part_of", "related", "unrelated"]
    part: Literal["a", "b", "none"] = Field(description="For part_of, which project is the part. Otherwise none.")
    confidence: Literal["high", "low"]
    description: str = Field(default="", description="One short sentence naming the dependency, or empty for unrelated.")


class NoteRelations(BaseModel):
    relations: list[Relation] = Field(default_factory=list)


class ProjectSource(BaseModel):
    path: str = Field(min_length=1, description="a working directory this project's sessions come from")
    prefix: str = Field(default="", description="when the repository holds several projects, the directory inside it that is this one")


class ProjectEntryWrite(BaseModel):
    slug: ProjectName
    description: str = Field(default="", description="what this project is, in the user's own words")
    sources: list[ProjectSource] = Field(default_factory=list)
    container: bool = Field(default=False, description="true when the repository itself is this project and others live inside it")


class ProjectEntryPublic(ProjectEntryWrite):
    id: UUID
    updated_at: datetime


class ProjectLinkWrite(BaseModel):
    kind: LinkKind
    source: ProjectName
    target: ProjectName
    description: str = ""
    stated_by_user: str = Field(default="", description="what the user wrote about the pair, kept so a rebuild reaches the same conclusion")
    confirmed: bool = True
    summarizer: str = ""


class ProjectLinkPublic(BaseModel):
    id: UUID
    kind: LinkKind
    source: str
    target: str
    description: str
    stated_by_user: str
    confirmed: bool
    unrelated: bool
    summarizer: str
    created_at: datetime


class ProjectOverview(SummaryResult):
    title: str = Field(description="The name of the project, not the title of a session.")
    summary: str = Field(description="Two to five sentences on what the project is and where it stands now.")
    observations: list[Observation] = Field(default_factory=list, description="What holds, what is open and what is queued for this project.")


class PromptWrite(PromptBase):
    pass


class PromptUpdate(BaseModel):
    label: str | None = None
    system: str | None = None
    instruction: str | None = None
    thinking: bool | None = None
    temperature: float | None = Field(default=None, ge=0.0, le=2.0)
    max_tokens: int | None = Field(default=None, ge=256)


class PromptPublic(PromptBase):
    id: UUID
    status: PromptStatus
    parent_id: UUID | None
    created_at: datetime
    updated_at: datetime


class TranscriptRef(BaseModel):
    agent: str = Field(min_length=1, max_length=100)
    device: str = Field(min_length=1, max_length=200)
    session_id: str = Field(min_length=1, max_length=200)
    project: str = ""
    cwd: str | None = None


class TranscriptState(BaseModel):
    id: UUID
    byte_size: int
    digest: str
    started_at: datetime | None
    ended_at: datetime | None


class UploadState(BaseModel):
    id: UUID
    kind: UploadKind
    byte_size: int
    part_count: int
    completed_at: datetime | None


class GitSourceState(BaseModel):
    network_id: UUID
    source_id: UUID
    namespace: str
    refs: dict[str, str] = Field(default_factory=dict)
    snapshot_ref: str | None = None


class GitIngestRequest(BaseModel):
    upload_id: UUID
    device: str
    path: str
    remote: str | None = None
    roots: list[str] = Field(default_factory=list, description="root commit hashes, which decide the network this repository belongs to")
    snapshot: bool = Field(default=False, description="true when the bundle carries a worktree snapshot commit instead of real history")


class GitQuery(BaseModel):
    network_id: UUID
    command: Literal["overview", "log", "show", "read_file", "grep", "worktree"]
    rev: str = ""
    path: str = ""
    pattern: str = ""
    since: str = ""
    until: str = ""
    start: int = Field(default=1, ge=1)
    end: int = Field(default=200, ge=1)


class GitQueryResult(BaseModel):
    output: str


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
    heartbeat_at: datetime | None
    attempts: int
    last_error: str | None
    summarizer: str | None
    note_path: str | None
    completed_at: datetime | None

    transcript: list[dict[str, Any]] | None = Field(default=None, exclude=True)


class JobPage(BaseModel):
    items: list[JobPublic]
    total: int


class NoteSummary(BaseModel):
    path: str
    title: str
    project: str
    tags: list[str] = Field(default_factory=list)
    source: dict[str, Any] = Field(default_factory=dict)

    @property
    def ordered_at(self) -> str:
        return str(self.source.get("ended_at") or self.source.get("started_at") or self.source.get("ingested_at") or "")

    @staticmethod
    def parse_markdown_file(path: Path, root: Path) -> tuple[dict[str, Any], str]:
        post = frontmatter.load(path)
        source = post.metadata.get("source")
        tags = post.metadata.get("tags")
        relative = path.relative_to(root).as_posix()
        fields = {
            "path": relative,
            "title": str(post.metadata.get("title") or path.stem),
            "project": project_of(relative),
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
    name: str = Field(description="the project's slug, which is also its directory under projects/")
    note_count: int
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


class ProjectDeleteResult(BaseModel):
    project: str
    deleted_notes: int


class NoteForget(BaseModel):
    paths: list[str] = Field(min_length=1, description="notes to remove, as the paths the listing shows")
    verdict: NoteVerdict = Field(default=NoteVerdict.HIDE, description="hide keeps the raw transcript, purge removes it so nothing can rebuild it")
    reason: str = ""


class NoteForgotten(BaseModel):
    path: str
    removed: bool
    raw_removed: bool


class NoteMove(BaseModel):
    path: str
    project: ProjectName
    first_request: int = Field(default=0, ge=0, description="the request this applies from; 0 pins the whole session")
    last_request: int = Field(default=0, ge=0, description="the last request it applies to; 0 means just first_request")


class NoteMoved(BaseModel):
    path: str
    project: str
    moved_to: str | None


class NotePinPublic(BaseModel):
    id: UUID
    agent: str
    device: str
    session_id: str
    project: str
    first_request: int
    last_request: int
    created_at: datetime


class NoteDecisionPublic(BaseModel):
    id: UUID
    agent: str
    device: str
    session_id: str
    verdict: NoteVerdict
    reason: str
    created_at: datetime


class MemoryContent(BaseModel):
    content: str


class SplitRow(BaseModel):
    number: int
    project: str
    how: Literal["path", "continue", "llm"]
    votes: dict[str, int] = Field(default_factory=dict)


class SessionPlan(BaseModel):
    segments: list[str] = Field(default_factory=list, description="the session's requests, each rendered as one '## 사용자 요청 #N' block")
    repository: str = Field(default="", description="what the project's repository holds right now: commits, touched files, uncommitted work")
    network_id: UUID | None = Field(default=None, description="pass this to /api/git/query to look the repository up")
    projects: list[str] = Field(default_factory=list, description="other known projects, one per line, for link candidates")
    started_at: datetime | None = None
    ended_at: datetime | None = None
    split: list[SplitRow] = Field(default_factory=list, description="which project each request belongs to; rows marked llm still need a decision")
    candidates: list[str] = Field(default_factory=list, description="the projects this repository holds, with what each one is")
    names: list[str] = Field(default_factory=list, description="the slugs of those projects, the only values a split may choose")
    verify_max_claims: int = Field(default=12, description="how many change and fact claims to check against the repository")


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
    plan: SessionPlan | None = Field(default=None, description="segments, repository state and link candidates a session job needs")
    prompts: dict[str, dict[str, Any]] = Field(default_factory=dict, description="the active prompt for each stage, with its model settings")


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
    display_timezone: str | None = None
    verify_max_claims: int | None = Field(default=None, ge=0)

    @field_validator("display_timezone")
    @classmethod
    def known_zone(cls, value: str | None) -> str | None:
        return None if value is None else RuntimeSettingBase.known_zone(value)

    rebuild_batch_size: int | None = Field(default=None, ge=0)
    rebuild_max_age_days: int | None = Field(default=None, ge=1)


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
