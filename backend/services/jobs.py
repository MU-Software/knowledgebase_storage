from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from pathlib import PurePosixPath
from typing import TYPE_CHECKING, Annotated, Any

from fastapi import Depends
from sqlalchemy import true
from sqlmodel import col

from backend.consts.notes import MEMORY_DIR, named_project
from backend.errors import ClientError
from backend.models import Job, JobBase, JobStatus
from backend.repositories.decision import noteDecisionRepositoryDI, notePinRepositoryDI
from backend.repositories.job import JobRepository, jobRepositoryDI
from backend.repositories.raw import rawTranscriptRepositoryDI
from backend.repositories.setting import llmProviderRepositoryDI, runtimeSettingRepositoryDI
from backend.schemas import MemoryFile, MemorySync, SessionNote
from backend.services import ServiceImpl
from backend.services.background import backgroundServiceDI
from backend.services.links import linkServiceDI
from backend.services.notes import NoteTarget, noteServiceDI
from backend.services.projects import projectServiceDI

if TYPE_CHECKING:
    from collections.abc import Sequence
    from uuid import UUID

    from backend.models import NotePin
    from backend.schemas import LinkVerdict, NoteRelations, SessionHeader, SessionItem, SessionNotes, SummaryResult

logger = logging.getLogger(__name__)

BACKOFF_BASE_SECONDS = 60
BACKOFF_CAP_SECONDS = 3600
SETTLED_STATUSES = {JobStatus.DONE, JobStatus.FAILED}
LAST_REQUEST_CHARS = 1000


def last_request_of(transcript: list[dict[str, Any]] | None) -> str | None:
    request = next((str(m.get("content", "")) for m in reversed(transcript or []) if m.get("role") == "user"), None)
    return None if request is None else request[:LAST_REQUEST_CHARS]


class JobService(ServiceImpl[JobRepository]):
    repository: jobRepositoryDI
    notes: noteServiceDI
    projects: projectServiceDI
    background: backgroundServiceDI
    providers: llmProviderRepositoryDI
    runtime: runtimeSettingRepositoryDI
    transcripts: rawTranscriptRepositoryDI
    links: linkServiceDI
    decisions: noteDecisionRepositoryDI
    pins: notePinRepositoryDI

    async def enqueue(self, payload: JobBase) -> tuple[Job, bool]:
        payload.project = await self.projects.resolve(named_project(payload.project))
        now, digest = datetime.now(UTC), payload.digest
        existing = await self.repository.find_by_session(payload.agent, payload.device, payload.session_id, lock=True)
        if await self.decisions.held_back(payload.agent, payload.device, payload.session_id):
            if existing is not None:
                return existing, False
            settled = Job(**payload.model_dump(exclude={"transcript"}), status=JobStatus.DONE, created_at=now, last_activity_at=now, completed_at=now)
            return await self.repository.save(settled), True
        if existing is None:
            job = Job(
                **payload.model_dump(),
                created_at=now,
                last_activity_at=now,
                transcript_digest=digest,
                last_request=last_request_of(payload.transcript),
            )
            return await self.repository.save(job), True
        if existing.transcript_digest == digest:
            return existing, False

        existing.sqlmodel_update(payload.model_dump(exclude_none=True))
        existing.transcript_digest = digest
        existing.last_request = last_request_of(payload.transcript)
        existing.last_activity_at = now
        existing.priority = 0
        existing.refined_at = None
        if existing.status in SETTLED_STATUSES:
            existing.status = JobStatus.PENDING
            existing.attempts = 0
            existing.last_error = None
            existing.next_attempt_at = None
        return await self.repository.save(existing), False

    async def retrieve(self, job_id: UUID, *, lock: bool = False) -> Job:
        return await self.repository.retrieve_by_id(job_id, with_for_update=lock)

    async def claim_held(self, job_id: UUID, token: UUID) -> Job:
        job = await self.retrieve(job_id, lock=True)
        self.verify_claim(job, token)
        return job

    async def list_jobs(self, job_status: JobStatus | None, offset: int, limit: int) -> tuple[Sequence[Job], int]:
        query_filter = true() if job_status is None else col(Job.status) == job_status
        return await self.repository.page(query_filter, offset, limit)

    def verify_claim(self, job: Job, token: UUID) -> None:
        if job.status is not JobStatus.CLAIMED or job.claim_token != token:
            ClientError.STALE_CLAIM.format_msg(job_id=job.id).raise_()

    async def release(self, job: Job, retry_after_seconds: int = 0) -> Job:
        job.status = JobStatus.PENDING
        job.claimed_at = None
        job.claimed_by = None
        job.claim_token = None
        job.next_attempt_at = datetime.now(UTC) + timedelta(seconds=retry_after_seconds) if retry_after_seconds else None
        return await self.repository.save(job)

    async def complete_session(self, job: Job, payload: SessionNotes, summarizer: str) -> Job:
        zone = (await self.runtime.get()).zone
        transcript = await self.transcripts.find_by_session(job.agent, job.device, job.session_id)
        window = (transcript.started_at, transcript.ended_at) if transcript else (job.started_at, job.ended_at)
        here = await self.projects.resolve(job.project)
        pins = await self.pins.around(job.agent, job.device, job.session_id)
        home = next((pin.project for pin in pins if pin.first_request == 0), here)
        wanted = self.repinned(payload.notes, pins, here) if pins else payload.notes
        if not wanted:
            return await self.fail(job, "the summary came back without a note")
        merged: dict[str, SessionNote] = {}
        for note in wanted:
            slug = here if not note.project else await self.projects.resolve(note.project)
            held = merged.get(slug)
            items = note.items if held is None else [*held.items, *note.items]
            merged[slug] = (held or note).model_copy(update={"project": slug, "items": items})
        own = merged.pop(home, None)
        written = {
            self.notes.write_session(job, note, summarizer, NoteTarget(project=slug, window=window, zone=zone, sidecar=True))
            for slug, note in merged.items()
        }
        if own is not None:
            job.note_path = self.notes.write_session(job, own, summarizer, NoteTarget(project=home, window=window, zone=zone))
            written.add(job.note_path)
        elif written:
            job.note_path = next(iter(sorted(written)))
        if written:
            self.notes.forget_others(job, written)
        elif job.note_path and self.notes.repository.resolve(job.note_path) is None:
            job.note_path = None
        await self.links.absorb(job, [*([own] if own else []), *merged.values()], summarizer)
        return await self.written(job, summarizer)

    @staticmethod
    def repinned(notes: list[SessionNote], pins: Sequence[NotePin], here: str) -> list[SessionNote]:
        grouped: dict[str, list[SessionItem]] = {}
        headers: dict[str, SessionHeader] = {}
        for note in notes:
            owner = note.project or here
            for item in note.items:
                project = next((pin.project for pin in pins if pin.holds(item.number)), owner)
                grouped.setdefault(project, []).append(item)
                headers.setdefault(project, note.header)
        return [SessionNote(header=headers[project], items=items, project=project) for project, items in grouped.items()]

    async def written(self, job: Job, summarizer: str) -> Job:
        job.summarizer = summarizer
        job.completed_at = datetime.now(UTC)
        job.last_error = None
        job.claim_token = None
        if job.claimed_at is not None and job.last_activity_at > job.claimed_at:
            # the session went on while this was being summarized, so summarize it again once it goes quiet
            job.status = JobStatus.PENDING
            job.claimed_at = None
            job.claimed_by = None
        else:
            job.status = JobStatus.DONE
        return await self.repository.save(job)

    async def complete(self, job: Job, result: SummaryResult, summarizer: str) -> Job:
        zone = (await self.runtime.get()).zone
        job.note_path = self.notes.write(job, result, summarizer, await self.projects.resolve(job.project), zone)
        return await self.written(job, summarizer)

    async def settle(self, job: Job, summarizer: str) -> Job:
        job.summarizer = summarizer
        job.completed_at = datetime.now(UTC)
        job.last_error = None
        job.claim_token = None
        job.status = JobStatus.DONE
        job.transcript = None
        return await self.repository.save(job)

    async def complete_memory(self, job: Job, content: str, summarizer: str) -> Job:
        if job.note_path is None or f"/{MEMORY_DIR}/" not in job.note_path:
            ClientError.INVALID_MEMORY_PATH.format_msg(job_id=job.id).raise_()
        if await self.background.moved_on(job):
            return await self.release(job)

        file = MemoryFile(name=PurePosixPath(job.note_path).name, content=self.notes.clean_memory(content))
        payload = MemorySync(agent=job.agent, device=job.device, project=job.project)
        self.notes.store(job.note_path, self.notes.render_memory(payload, file))
        return await self.settle(job, summarizer)

    async def complete_overview(self, job: Job, result: SummaryResult, summarizer: str) -> Job:
        job.note_path = self.background.write_overview(job, result, summarizer)
        moved_on = await self.background.moved_on(job)
        await self.background.cascade(job)
        return await self.release(job) if moved_on else await self.settle(job, summarizer)

    async def complete_link(self, job: Job, verdict: LinkVerdict, summarizer: str) -> Job:
        await self.links.judged(job.note_path or "", verdict, summarizer)
        return await self.settle(job, summarizer)

    async def complete_relations(self, job: Job, result: NoteRelations, summarizer: str) -> Job:
        self.background.write_relations(job, result, summarizer)
        return await self.settle(job, summarizer)

    async def fail(self, job: Job, error: str) -> Job:
        job.last_error = error
        job.claimed_at = None
        job.claimed_by = None
        job.claim_token = None
        job.attempts += 1
        limit = (await self.runtime.get()).job_max_attempts
        if job.attempts < limit:
            job.status = JobStatus.PENDING
            job.next_attempt_at = datetime.now(UTC) + self.backoff(job.attempts)
            return await self.repository.save(job)

        settled = job.refined_at is not None and job.completed_at is not None
        job.status = JobStatus.DONE if settled else JobStatus.FAILED
        job.next_attempt_at = None
        return await self.repository.save(job)

    @staticmethod
    def backoff(attempts: int) -> timedelta:
        return timedelta(seconds=min(BACKOFF_BASE_SECONDS * 2 ** (attempts - 1), BACKOFF_CAP_SECONDS))

    async def claim_next(self, worker: str, provider_name: str | None, min_age_seconds: int = 0) -> Job | None:
        idle = timedelta(seconds=max((await self.runtime.get()).job_idle_seconds, min_age_seconds))
        provider = await self.providers.find_by_name(provider_name) if provider_name else None
        background = provider is not None and provider.background_jobs
        return await self.repository.claim_next(worker, idle, background=background, priority=provider.priority if provider else None)


jobServiceDI = Annotated[JobService, Depends(JobService)]  # noqa: N816
