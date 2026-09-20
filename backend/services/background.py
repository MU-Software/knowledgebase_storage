from __future__ import annotations

from collections import Counter
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Annotated, Any, Self

from fastapi import Depends

from backend.consts.notes import LOG_DIR, UNFILED, ancestors_of, is_within, parent_of, slugify
from backend.models import BACKGROUND_AGENT, BACKGROUND_DEVICE, Job, JobKind, ProjectSuggestion, SuggestionKind, background_session
from backend.repositories.alias import ProjectAliasRepository
from backend.repositories.job import JobRepository, jobRepositoryDI
from backend.repositories.note import NoteRepository
from backend.repositories.project import ProjectRepository
from backend.repositories.setting import LLMProviderRepository, RuntimeSettingRepository, llmProviderRepositoryDI, runtimeSettingRepositoryDI
from backend.repositories.suggestion import ProjectSuggestionRepository, projectSuggestionRepositoryDI
from backend.schemas import JobClaimed
from backend.services import ServiceImpl
from backend.services.context import ContextService, contextServiceDI
from backend.services.notes import NoteService, noteServiceDI
from backend.services.projects import ProjectService, leaf_of, projectServiceDI

if TYPE_CHECKING:
    from pathlib import Path

    from sqlmodel.ext.asyncio.session import AsyncSession

    from backend.models import RuntimeSetting
    from backend.schemas import NoteRelations, ProjectSuggestions, SummaryResult

RERUN_ON_CHANGE = frozenset({JobKind.MEMORY_MERGE, JobKind.PROJECT_OVERVIEW})
OVERVIEW_LOG_LIMIT = 40
CATALOG_CHARS = 600
RELATION_CANDIDATE_LIMIT = 120
SUGGESTION_MIN_PROJECTS = 3
ALL_PROJECTS = "_all"


def record(heading: str, body: str) -> dict[str, Any]:
    return {"role": "user", "content": f"## {heading}\n\n{body.strip()}"}


class BackgroundService(ServiceImpl[JobRepository]):
    repository: jobRepositoryDI
    projects: projectServiceDI
    notes: noteServiceDI
    suggestions: projectSuggestionRepositoryDI
    providers: llmProviderRepositoryDI
    runtime: runtimeSettingRepositoryDI
    context: contextServiceDI

    @classmethod
    def for_session(cls, session: AsyncSession, notes_dir: Path) -> Self:
        notes = NoteService(repository=NoteRepository(root=notes_dir))
        jobs = JobRepository(session=session)
        suggestions = ProjectSuggestionRepository(session=session)
        projects = ProjectService(
            repository=ProjectRepository(root=notes_dir),
            aliases=ProjectAliasRepository(session=session),
            jobs=jobs,
            suggestions=suggestions,
            notes=notes,
        )
        return cls(
            repository=jobs,
            projects=projects,
            notes=notes,
            suggestions=suggestions,
            providers=LLMProviderRepository(session=session),
            runtime=RuntimeSettingRepository(session=session),
            context=ContextService(repository=jobs, notes=notes, projects=projects),
        )

    async def sweep(self) -> dict[str, int]:
        runtime = await self.runtime.get()
        room = runtime.background_batch_size - await self.repository.count_pending_background()
        if room <= 0:
            return {}

        done: dict[str, int] = {}
        for name, sweep in (
            ("overview", self.sweep_overviews),
            ("suggestion", self.sweep_suggestions),
            ("refine", self.sweep_refinements),
            ("relations", self.sweep_relations),
        ):
            if room <= 0:
                break
            if enqueued := await sweep(runtime, room):
                done[name] = enqueued
                room -= enqueued
        return done

    def log_counts(self) -> Counter[str]:
        return Counter(summary.project_path for summary in self.notes.list_notes() if f"/{LOG_DIR}/" in summary.path)

    def stale(self, project_path: str, logs: int, runtime: RuntimeSetting) -> bool:
        overview = self.notes.overview(project_path)
        if overview is None:
            return True
        source = overview.source
        if logs - int(source.get("logs") or 0) >= runtime.overview_min_new_logs:
            return True
        try:
            written = datetime.fromisoformat(str(source.get("generated_at")))
        except ValueError:
            return True
        return written < datetime.now(UTC) - timedelta(days=runtime.overview_max_age_days)

    async def sweep_overviews(self, runtime: RuntimeSetting, room: int) -> int:
        counts = self.log_counts()
        enqueued = 0
        for path in self.projects.repository.list_paths():
            if enqueued >= room:
                break
            if self.stale(path, counts[path], runtime) and await self.projects.request_overview(leaf_of(path)) is not None:
                enqueued += 1
        return enqueued

    async def sweep_suggestions(self, runtime: RuntimeSetting, room: int) -> int:
        paths = self.projects.repository.list_paths()
        if room < 1 or len(paths) < SUGGESTION_MIN_PROJECTS:
            return 0

        session_id = background_session(JobKind.PROJECT_SUGGESTION)
        previous = await self.repository.find_by_session(BACKGROUND_AGENT, BACKGROUND_DEVICE, session_id)
        swept_at = previous.completed_at if previous else None
        if swept_at is not None and swept_at > datetime.now(UTC) - timedelta(hours=runtime.background_sweep_hours):
            return 0
        return 1 if await self.repository.ensure_background(JobKind.PROJECT_SUGGESTION, session_id, ALL_PROJECTS) else 0

    async def sweep_refinements(self, _runtime: RuntimeSetting, room: int) -> int:
        providers = await self.providers.list_active()
        best = min((provider.priority for provider in providers if provider.background_jobs), default=None)
        if best is None:
            return 0
        weaker = [provider.name for provider in providers if provider.priority > best]
        if not weaker:
            return 0

        jobs = await self.repository.refinable(weaker, room)
        for job in jobs:
            await self.repository.reopen(job)
        return len(jobs)

    async def sweep_relations(self, _runtime: RuntimeSetting, room: int) -> int:
        enqueued = 0
        for summary in self.notes.list_notes():
            if enqueued >= room:
                break
            if f"/{LOG_DIR}/" not in summary.path or summary.source.get("relations_at"):
                continue
            session_id = background_session(JobKind.NOTE_RELATIONS, summary.path)
            if await self.repository.ensure_background(JobKind.NOTE_RELATIONS, session_id, summary.project, summary.path) is not None:
                enqueued += 1
        return enqueued

    @staticmethod
    def fingerprint(records: list[dict[str, Any]], targets: list[str]) -> str:
        return Job.digest_of([*records, {"targets": targets}])

    async def claimed(self, job: Job) -> JobClaimed:
        records, targets = self.payload(job)
        extra: dict[str, object] = {"transcript": records, "targets": targets}
        if job.kind is JobKind.SESSION:
            extra["context"] = await self.context.for_job(job)
        if job.kind in RERUN_ON_CHANGE:
            job.transcript_digest = self.fingerprint(records, targets)
            await self.repository.save(job)
        return JobClaimed.model_validate(job, from_attributes=True).model_copy(update=extra)

    def moved_on(self, job: Job) -> bool:
        return job.kind in RERUN_ON_CHANGE and job.transcript_digest != self.fingerprint(*self.payload(job))

    def payload(self, job: Job) -> tuple[list[dict[str, Any]], list[str]]:
        if job.kind is JobKind.PROJECT_OVERVIEW:
            return self.overview_payload(job.project)
        if job.kind is JobKind.PROJECT_SUGGESTION:
            return self.catalog_payload()
        if job.kind is JobKind.NOTE_RELATIONS:
            return self.relation_payload(job.note_path or "")
        if job.kind is JobKind.MEMORY_MERGE:
            return self.memory_payload(job), []
        return job.transcript or [], []

    def memory_payload(self, job: Job) -> list[dict[str, Any]]:
        current = self.notes.repository.resolve(job.note_path or "")
        if current is None:
            return job.transcript or []
        standing = self.notes.memory_document(job.project, current.read_text(encoding="utf-8"))
        return [standing, *(job.transcript or [])]

    def overview_payload(self, slug: str) -> tuple[list[dict[str, Any]], list[str]]:
        path = self.projects.path_of(slug)
        children = self.projects.repository.children(path)
        above = [(f"the project this one sits under: {leaf_of(other)}", other) for other in ancestors_of(path)[:1]]
        below = [(f"a project inside this one: {leaf_of(other)}", other) for other in children]
        records: list[dict[str, Any]] = [
            record(heading, page.content) for heading, other in above + below if (page := self.notes.overview(other)) is not None
        ]

        for note in self.notes.recent_logs(path, OVERVIEW_LOG_LIMIT, subtree=False):
            summary, observations = self.notes.parse_log(note.content)
            kept = "\n".join(f"- [{o.category}] {o.text}" for o in observations)
            records.append(record(note.title, f"{summary}\n{kept}"))
        return records, [leaf_of(child) for child in children]

    def catalog_payload(self) -> tuple[list[dict[str, Any]], list[str]]:
        counts = self.log_counts()
        records = []
        for path in self.projects.repository.list_paths():
            overview = self.notes.overview(path)
            recent = [note.title for note in self.notes.recent_logs(path, 3, subtree=False)]
            about = overview.content[:CATALOG_CHARS] if overview else "\n".join(f"- {title}" for title in recent)
            where = f" (filed under {path.rpartition('/')[0]})" if "/" in path else ""
            records.append(record(f"{leaf_of(path)}{where}", f"{counts[path]} sessions\n{about}"))
        return records, []

    def relation_payload(self, note_path: str) -> tuple[list[dict[str, Any]], list[str]]:
        note = self.notes.retrieve(note_path)
        family = [note.project_path, *ancestors_of(note.project_path)]
        kin = [
            summary
            for summary in self.notes.list_notes()
            if summary.path != note_path and f"/{LOG_DIR}/" in summary.path and any(is_within(summary.project_path, branch) for branch in family)
        ]
        kin.sort(key=lambda summary: str(summary.source.get("ingested_at", "")), reverse=True)
        return [record(note.title, note.content)], [summary.title for summary in kin[:RELATION_CANDIDATE_LIMIT]]

    def catalog_names(self) -> set[str]:
        return {leaf_of(path) for path in self.projects.repository.list_paths()} - {UNFILED}

    def write_overview(self, job: Job, result: SummaryResult, summarizer: str) -> str:
        path = self.projects.path_of(job.project)
        children = [leaf_of(other) for other in self.projects.repository.children(path)]
        content = self.notes.render_overview(job.project, result, summarizer, logs=self.log_counts()[path], children=children)
        return self.notes.store(self.notes.overview_path(path), content)

    def write_relations(self, job: Job, result: NoteRelations, summarizer: str) -> int:
        return self.notes.apply_relations(job.note_path or "", result.relations, summarizer)

    async def cascade(self, job: Job) -> None:
        if (above := parent_of(self.projects.path_of(job.project))) is not None:
            await self.projects.request_overview(leaf_of(above))

    def redundant(self, kind: SuggestionKind, source: str, target: str) -> bool:
        held, holder = self.projects.path_of(source), self.projects.path_of(target)
        if kind is SuggestionKind.NEST:
            return is_within(held, holder) or is_within(holder, held)
        return is_within(holder, held)

    async def apply_suggestions(self, result: ProjectSuggestions, summarizer: str) -> int:
        names = self.catalog_names()
        stored = 0
        for item in result.suggestions:
            source, target = slugify(item.source), slugify(item.target)
            if source == target or not {source, target} <= names or self.redundant(item.kind, source, target):
                continue
            if await self.suggestions.seen(item.kind, source, target) or await self.suggestions.seen(item.kind, target, source):
                continue
            suggestion = ProjectSuggestion(kind=item.kind, source=source, target=target, reason=item.reason, summarizer=summarizer)
            await self.suggestions.save(suggestion)
            stored += 1
        return stored


backgroundServiceDI = Annotated[BackgroundService, Depends(BackgroundService)]  # noqa: N816
