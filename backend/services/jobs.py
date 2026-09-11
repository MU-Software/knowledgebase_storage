from __future__ import annotations

import json
import logging
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from typing import TYPE_CHECKING, Annotated, Any

from fastapi import Depends
from sqlalchemy import true
from sqlmodel import col

from backend.errors import ClientError
from backend.models import Job, JobBase, JobStatus
from backend.repositories.job import JobRepository, jobRepositoryDI
from backend.repositories.setting import runtimeSettingRepositoryDI
from backend.services import ServiceImpl
from backend.services.notes import noteServiceDI

if TYPE_CHECKING:
    from collections.abc import Sequence
    from uuid import UUID

    from backend.schemas import SummaryResult

logger = logging.getLogger(__name__)

BACKOFF_BASE_SECONDS = 60
BACKOFF_CAP_SECONDS = 3600
SETTLED_STATUSES = {JobStatus.DONE, JobStatus.FAILED}
LAST_REQUEST_CHARS = 1000


def transcript_digest(transcript: list[dict[str, Any]] | None) -> str:
    return sha256(json.dumps(transcript, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def last_request_of(transcript: list[dict[str, Any]] | None) -> str | None:
    request = next((str(m.get("content", "")) for m in reversed(transcript or []) if m.get("role") == "user"), None)
    return None if request is None else request[:LAST_REQUEST_CHARS]


class JobService(ServiceImpl[JobRepository]):
    repository: jobRepositoryDI
    notes: noteServiceDI
    runtime: runtimeSettingRepositoryDI

    async def enqueue(self, payload: JobBase) -> tuple[Job, bool]:
        now, digest = datetime.now(UTC), transcript_digest(payload.transcript)
        existing = await self.repository.find_by_session(payload.agent, payload.device, payload.session_id, lock=True)
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

    async def list_jobs(self, job_status: JobStatus | None, limit: int) -> Sequence[Job]:
        query_filter = true() if job_status is None else col(Job.status) == job_status
        return await self.repository.list(query_filter=query_filter, limit=limit)

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

    async def complete(self, job: Job, result: SummaryResult, summarizer: str) -> Job:
        job.note_path = self.notes.write(job, result, summarizer)
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
            job.transcript = None
        return await self.repository.save(job)

    async def fail(self, job: Job, error: str) -> Job:
        job.last_error = error
        job.claimed_at = None
        job.claimed_by = None
        job.claim_token = None
        job.attempts += 1
        limit = (await self.runtime.get()).job_max_attempts
        job.status = JobStatus.FAILED if job.attempts >= limit else JobStatus.PENDING
        job.next_attempt_at = None if job.status is JobStatus.FAILED else datetime.now(UTC) + self.backoff(job.attempts)
        return await self.repository.save(job)

    @staticmethod
    def backoff(attempts: int) -> timedelta:
        return timedelta(seconds=min(BACKOFF_BASE_SECONDS * 2 ** (attempts - 1), BACKOFF_CAP_SECONDS))

    async def claim_next(self, worker: str) -> Job | None:
        idle = timedelta(seconds=(await self.runtime.get()).job_idle_seconds)
        return await self.repository.claim_next(worker, idle)


jobServiceDI = Annotated[JobService, Depends(JobService)]  # noqa: N816
