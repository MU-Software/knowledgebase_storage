from __future__ import annotations

import asyncio
import logging
from collections import Counter
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Annotated, Any, Self

from fastapi import Depends

from backend.consts.notes import LOG_DIR, UNFILED
from backend.models import BACKGROUND_AGENT, BACKGROUND_DEVICE, Job, JobKind, JobStatus, background_session
from backend.repositories.alias import ProjectAliasRepository
from backend.repositories.decision import NotePinRepository
from backend.repositories.entry import ProjectEntryRepository
from backend.repositories.git import GitNetworkRepository, GitRepository, GitSourceRepository
from backend.repositories.job import JobRepository, jobRepositoryDI
from backend.repositories.link import ProjectLinkRepository
from backend.repositories.note import NoteRepository
from backend.repositories.project import ProjectRepository
from backend.repositories.prompt import PromptRepository
from backend.repositories.raw import RawTranscriptRepository, UploadRepository, rawTranscriptRepositoryDI
from backend.repositories.setting import LLMProviderRepository, RuntimeSettingRepository, llmProviderRepositoryDI, runtimeSettingRepositoryDI
from backend.repositories.storage import StorageRepository
from backend.schemas import JobClaimed, SessionPlan, SplitRow
from backend.services import ServiceImpl
from backend.services.context import ContextService, contextServiceDI
from backend.services.entries import EntryService, entryServiceDI
from backend.services.extract import ExtractService, extractServiceDI
from backend.services.git import GitService, gitServiceDI
from backend.services.links import LinkService, linkServiceDI
from backend.services.notes import NoteService, noteServiceDI
from backend.services.projects import ProjectService, projectServiceDI
from backend.services.prompts import PromptService, promptServiceDI
from backend.services.split import SplitService, splitServiceDI

if TYPE_CHECKING:
    from pathlib import Path

    from sqlmodel.ext.asyncio.session import AsyncSession

    from backend.models import RuntimeSetting
    from backend.schemas import NoteRelations, SummaryResult

logger = logging.getLogger(__name__)

RERUN_ON_CHANGE = frozenset({JobKind.MEMORY_MERGE, JobKind.PROJECT_OVERVIEW})
OVERVIEW_LOG_LIMIT = 40
CATALOG_CHARS = 600
PAIR_LOG_LIMIT = 3
RELATION_CANDIDATE_LIMIT = 120
TRIVIAL_SCAN = 10


def is_nested(path: str, other: str) -> bool:
    return path != other and (other.startswith(path + "/") or path.startswith(other + "/"))


def record(heading: str, body: str) -> dict[str, Any]:
    return {"role": "user", "content": f"## {heading}\n\n{body.strip()}"}


class BackgroundService(ServiceImpl[JobRepository]):
    repository: jobRepositoryDI
    projects: projectServiceDI
    notes: noteServiceDI
    providers: llmProviderRepositoryDI
    runtime: runtimeSettingRepositoryDI
    context: contextServiceDI
    extract: extractServiceDI
    git: gitServiceDI
    prompts: promptServiceDI
    transcripts: rawTranscriptRepositoryDI
    links: linkServiceDI
    entries: entryServiceDI
    split: splitServiceDI

    @classmethod
    def for_session(cls, session: AsyncSession, notes_dir: Path, storage_dir: Path) -> Self:
        notes = NoteService(repository=NoteRepository(root=notes_dir))
        jobs = JobRepository(session=session)
        storage = StorageRepository(root=storage_dir)
        runtime_settings = RuntimeSettingRepository(session=session)
        links = ProjectLinkRepository(session=session)
        entries = ProjectEntryRepository(session=session)
        projects = ProjectService(
            repository=ProjectRepository(root=notes_dir),
            aliases=ProjectAliasRepository(session=session),
            jobs=jobs,
            links=links,
            entries=entries,
            pins=NotePinRepository(session=session),
            notes=notes,
        )
        link_service = LinkService(repository=links, projects=projects)
        return cls(
            repository=jobs,
            projects=projects,
            notes=notes,
            providers=LLMProviderRepository(session=session),
            runtime=runtime_settings,
            context=ContextService(repository=jobs, notes=notes, projects=projects, links=link_service, runtime=runtime_settings),
            extract=ExtractService(repository=storage),
            git=GitService(
                repository=GitRepository(root=storage_dir),
                networks=GitNetworkRepository(session=session),
                sources=GitSourceRepository(session=session),
                uploads=UploadRepository(session=session),
                storage=storage,
            ),
            prompts=PromptService(repository=PromptRepository(session=session), runtime=RuntimeSettingRepository(session=session)),
            transcripts=RawTranscriptRepository(session=session),
            links=link_service,
            entries=EntryService(repository=entries, projects=projects),
            split=SplitService(repository=ProjectRepository(root=notes_dir)),
        )

    async def settle_trivial(self, runtime: RuntimeSetting) -> int:
        settled = 0
        for job in await self.repository.pending_sessions(TRIVIAL_SCAN, timedelta(seconds=runtime.job_idle_seconds)):
            seen = job.last_activity_at
            transcript = await self.transcripts.find_by_session(job.agent, job.device, job.session_id)
            if (
                transcript is None
                or not self.extract.stored(transcript)
                or self.extract.substantial(await asyncio.to_thread(self.extract.read, transcript))
            ):
                job.updated_at = datetime.now(UTC)
                await self.repository.save(job)
                continue
            if await self.repository.settle_without_note(job, seen):
                logger.info("session %s asked for nothing worth a note", job.session_id)
                settled += 1
        return settled

    async def sweep(self) -> dict[str, int]:
        runtime = await self.runtime.get()
        done: dict[str, int] = {}
        if hidden := await self.repository.settle_decided():
            done["hidden"] = hidden
        if trivial := await self.settle_trivial(runtime):
            done["trivial"] = trivial
        room = runtime.background_batch_size - await self.repository.count_pending_background()
        if room <= 0:
            return done

        if rolled := await self.sweep_rebuilds(runtime, room):
            done["rebuild"] = rolled
            room -= rolled
        for name, sweep in (
            ("overview", self.sweep_overviews),
            ("links", self.sweep_links),
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
        return Counter(summary.project for summary in self.notes.list_notes() if f"/{LOG_DIR}/" in summary.path)

    def stale(self, project: str, logs: int, runtime: RuntimeSetting) -> bool:
        overview = self.notes.overview(project)
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
        for slug in self.projects.repository.slugs():
            if enqueued >= room:
                break
            if not self.stale(slug, counts[slug], runtime):
                continue
            if await self.tried_lately(background_session(JobKind.PROJECT_OVERVIEW, slug), runtime, counting_done=False):
                continue
            if await self.projects.request_overview(slug) is not None:
                enqueued += 1
        return enqueued

    @staticmethod
    def combined(groups: list[set[str]]) -> set[tuple[str, str]]:
        return {(first, second) for held in groups for first in held for second in held if first < second}

    async def pairs_worth_judging(self) -> list[tuple[str, str]]:
        owners: dict[str, set[str]] = {}
        for entry in await self.entries.listed():
            for source in entry.sources:
                if path := source.get("path"):
                    owners.setdefault(path, set()).add(entry.slug)

        nested = [held | outside for path, held in owners.items() for other, outside in owners.items() if is_nested(path, other)]
        wanted = self.combined([*owners.values(), *nested]) | await self.git.shared_pairs()
        known = {(link.source, link.target) for link in await self.links.repository.all_links()}
        known |= {(target, source) for source, target in known}
        return sorted(pair for pair in wanted if pair not in known)

    async def tried_lately(self, session_id: str, runtime: RuntimeSetting, *, counting_done: bool) -> bool:
        previous = await self.repository.find_by_session(BACKGROUND_AGENT, BACKGROUND_DEVICE, session_id)
        if previous is None:
            return False
        stamp: datetime | None
        if previous.status is JobStatus.FAILED:
            stamp = previous.updated_at
        elif counting_done and previous.status is JobStatus.DONE:
            stamp = previous.completed_at
        else:
            return False
        return stamp is not None and stamp > datetime.now(UTC) - timedelta(hours=runtime.background_sweep_hours)

    async def sweep_links(self, runtime: RuntimeSetting, room: int) -> int:
        enqueued = 0
        for left, right in await self.pairs_worth_judging():
            if enqueued >= room:
                break
            pair = f"{left}~{right}"
            session_id = background_session(JobKind.PROJECT_LINK, pair)
            if await self.tried_lately(session_id, runtime, counting_done=True):
                continue
            if await self.repository.ensure_background(JobKind.PROJECT_LINK, session_id, left, pair) is not None:
                enqueued += 1
        return enqueued

    async def sweep_rebuilds(self, runtime: RuntimeSetting, room: int) -> int:
        allowed = min(runtime.rebuild_batch_size, room)
        if allowed <= 0:
            return 0
        stale = datetime.now(UTC) - timedelta(days=runtime.rebuild_max_age_days)
        rebuilt = 0
        for job in await self.repository.summarized_before(stale, allowed):
            transcript = await self.transcripts.find_by_session(job.agent, job.device, job.session_id)
            if transcript is None:
                continue
            if not self.extract.stored(transcript):
                logger.warning("session %s has no stored transcript file, so its rebuild waits another round", job.session_id)
                job.completed_at = datetime.now(UTC)
                await self.repository.save(job)
                continue
            await self.repository.requeue(job)
            rebuilt += 1
        return rebuilt

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

    async def sweep_relations(self, runtime: RuntimeSetting, room: int) -> int:
        enqueued = 0
        for summary in self.notes.list_notes():
            if enqueued >= room:
                break
            if f"/{LOG_DIR}/" not in summary.path or summary.source.get("relations_at"):
                continue
            session_id = background_session(JobKind.NOTE_RELATIONS, summary.path)
            if await self.tried_lately(session_id, runtime, counting_done=False):
                continue
            if await self.repository.ensure_background(JobKind.NOTE_RELATIONS, session_id, summary.project, summary.path) is not None:
                enqueued += 1
        return enqueued

    @staticmethod
    def fingerprint(records: list[dict[str, Any]], targets: list[str]) -> str:
        return Job.digest_of([*records, {"targets": targets}])

    async def session_plan(self, job: Job) -> SessionPlan:
        transcript = await self.transcripts.find_by_session(job.agent, job.device, job.session_id)
        if transcript is None:
            return SessionPlan()
        reader = self.extract.read(transcript)
        started, ended = self.extract.window(reader)
        state, network = await self.git.state_of(job.device, transcript.cwd or "")
        segments = [segment.render() for segment in reader.segments]
        prefixes, container, described = await self.entries.candidates(transcript.cwd or "")
        split = self.split.plan(segments, prefixes, container or job.project) if prefixes else []
        if not segments:
            logger.warning("session %s has no readable request, so it will not be summarized", job.session_id)
        return SessionPlan(
            segments=segments,
            split=[SplitRow.model_validate(row) for row in split],
            candidates=described,
            names=self.entries.named(described),
            verify_max_claims=(await self.runtime.get()).verify_max_claims,
            repository=state,
            network_id=network,
            projects=[f"- {name}" for name in sorted(self.catalog_names() - {job.project})],
            started_at=started,
            ended_at=ended,
        )

    async def payload_for(self, job: Job) -> tuple[list[dict[str, Any]], list[str]]:
        if job.kind is JobKind.PROJECT_OVERVIEW:
            return await self.overview_payload_with_links(job.project)
        if job.kind is JobKind.PROJECT_LINK:
            return await self.pair_payload(job)
        if job.kind is JobKind.NOTE_RELATIONS:
            return await self.relation_payload(job.note_path or "")
        return self.payload(job)

    async def claimed(self, job: Job) -> JobClaimed:
        records, targets = await self.payload_for(job)
        extra: dict[str, object] = {"transcript": records, "targets": targets, "prompts": await self.prompts.resolved()}
        if job.kind is JobKind.SESSION:
            extra["context"] = await self.context.for_job(job)
            extra["plan"] = await self.session_plan(job)
        if job.kind in RERUN_ON_CHANGE:
            job.transcript_digest = self.fingerprint(records, targets)
            await self.repository.save(job)
        return JobClaimed.model_validate(job, from_attributes=True).model_copy(update=extra)

    async def moved_on(self, job: Job) -> bool:
        return job.kind in RERUN_ON_CHANGE and job.transcript_digest != self.fingerprint(*await self.payload_for(job))

    def payload(self, job: Job) -> tuple[list[dict[str, Any]], list[str]]:
        if job.kind is JobKind.MEMORY_MERGE:
            return self.memory_payload(job), []
        return job.transcript or [], []

    def memory_payload(self, job: Job) -> list[dict[str, Any]]:
        current = self.notes.repository.resolve(job.note_path or "")
        if current is None:
            return job.transcript or []
        standing = self.notes.memory_document(job.project, current.read_text(encoding="utf-8"))
        return [standing, *(job.transcript or [])]

    async def overview_payload_with_links(self, slug: str) -> tuple[list[dict[str, Any]], list[str]]:
        records, targets = self.overview_payload(slug)
        if lines := await self.links.neighbours(slug):
            records.append(record("the projects this one is linked to", "\n".join(lines)))
        return records, targets

    def overview_payload(self, slug: str) -> tuple[list[dict[str, Any]], list[str]]:
        records: list[dict[str, Any]] = []
        for note in self.notes.recent_logs(slug, OVERVIEW_LOG_LIMIT):
            summary, observations = self.notes.parse_log(note.content)
            kept = "\n".join(f"- [{o.category}] {o.text}" for o in observations)
            records.append(record(note.title, f"{summary}\n{kept}"))
        return records, []

    async def pair_payload(self, job: Job) -> tuple[list[dict[str, Any]], list[str]]:
        left, _, right = (job.note_path or "").partition("~")
        if not left or not right:
            return [], []
        records = [record(f"Project {label}", await self.pair_record(name)) for label, name in (("a", left), ("b", right))]
        if said := await self.links.statements(left, right):
            records.append(record("Statements from the user or from sessions", said))
        return records, [left, right]

    async def pair_record(self, name: str) -> str:
        overview = self.notes.overview(name)
        described = await self.entries.describe([name])
        recent = [note.title for note in self.notes.recent_logs(name, PAIR_LOG_LIMIT)]
        about = overview.content[:CATALOG_CHARS] if overview else "\n".join(f"- {title}" for title in recent)
        sources = [source for entry in await self.entries.listed() if entry.slug == name for source in entry.sources]
        where = ", ".join(str(source.get("path", "")) for source in sources) or "unknown"
        return f"name: {name}\n{described[0] if described else ''}\nworking directories: {where}\n{about}"

    async def relation_payload(self, note_path: str) -> tuple[list[dict[str, Any]], list[str]]:
        note = self.notes.retrieve(note_path)
        family = {note.project, *await self.links.linked(note.project)}
        kin = [
            summary
            for summary in self.notes.list_notes()
            if summary.path != note_path and f"/{LOG_DIR}/" in summary.path and summary.project in family
        ]
        return [record(note.title, note.content)], [summary.title for summary in kin[:RELATION_CANDIDATE_LIMIT]]

    def catalog_names(self) -> set[str]:
        return set(self.projects.repository.slugs()) - {UNFILED}

    def write_overview(self, job: Job, result: SummaryResult, summarizer: str) -> str:
        slug = job.project
        content = self.notes.render_overview(slug, result, summarizer, logs=self.log_counts()[slug])
        return self.notes.store(self.notes.overview_path(slug), content)

    def write_relations(self, job: Job, result: NoteRelations, summarizer: str) -> int:
        return self.notes.apply_relations(job.note_path or "", result.relations, summarizer)

    async def cascade(self, job: Job) -> None:
        for holder in await self.links.holders(job.project):
            await self.projects.request_overview(holder)


backgroundServiceDI = Annotated[BackgroundService, Depends(BackgroundService)]  # noqa: N816
