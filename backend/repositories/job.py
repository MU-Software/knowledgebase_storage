from __future__ import annotations

from datetime import UTC, datetime
from pathlib import PurePosixPath
from typing import TYPE_CHECKING, Annotated
from uuid import uuid4

from fastapi import Depends
from sqlalchemy import delete, false, func, true, update
from sqlalchemy.orm import defer
from sqlmodel import col, desc, select
from sqlmodel.sql.expression import and_, or_

from backend.models import (
    BACKGROUND_AGENT,
    BACKGROUND_DEVICE,
    BACKGROUND_PRIORITY,
    Job,
    JobKind,
    JobStatus,
    LLMProvider,
    ProjectInference,
    background_session,
)
from backend.repositories import DBRepositoryImpl, OrderByType

if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import timedelta
    from typing import Any


class JobRepository(DBRepositoryImpl[Job]):
    model = Job
    resource = "job"

    @property
    def order_by(self) -> OrderByType:
        return [desc(col(Job.created_at))]

    async def find_by_session(self, agent: str, device: str, session_id: str, *, lock: bool = False) -> Job | None:
        query = select(Job).where(
            col(Job.agent) == agent,
            col(Job.device) == device,
            col(Job.session_id) == session_id,
        )
        if lock:
            query = query.with_for_update()
        return (await self.session.exec(query)).first()

    async def list_unsummarized(self, project: str, exclude_session_id: str | None, limit: int) -> Sequence[Job]:
        query = (
            select(Job)
            .options(defer(Job.transcript))  # type: ignore[arg-type]
            .where(
                col(Job.kind) == JobKind.SESSION,
                col(Job.project) == project,
                col(Job.status).in_([JobStatus.PENDING, JobStatus.CLAIMED]),
                col(Job.session_id) != (exclude_session_id or ""),
            )
            .order_by(desc(col(Job.last_activity_at)))
            .limit(limit)
        )
        return (await self.session.exec(query)).all()

    async def relink(self, moves: dict[str, str]) -> None:
        for old, new in moves.items():
            await self.session.exec(update(Job).where(col(Job.note_path) == old).values(note_path=new))
        await self.session.commit()

    async def rename_project(self, source: str, target: str) -> int:
        return await self.bulk_update(col(Job.project) == source, project=target)

    async def delete_project(self, slugs: list[str]) -> int:
        result = await self.session.exec(delete(Job).where(col(Job.project).in_(slugs)))
        await self.session.commit()
        return int(result.rowcount or 0)

    async def claim_next(self, worker: str, idle: timedelta, *, background: bool, priority: int | None) -> Job | None:
        now = datetime.now(UTC)
        worse = false() if priority is None else col(Job.summarizer).in_(select(col(LLMProvider.name)).where(col(LLMProvider.priority) > priority))
        pending = await self.list(
            query_filter=and_(
                col(Job.status) == JobStatus.PENDING,
                or_(col(Job.kind) != JobKind.SESSION, col(Job.last_activity_at) <= now - idle),
                or_(col(Job.next_attempt_at).is_(None), col(Job.next_attempt_at) <= now),
                true() if background else col(Job.priority) == 0,
                or_(col(Job.refined_at).is_(None), worse),
            ),
            order_by=[col(Job.priority), col(Job.created_at)],
            limit=1,
            with_for_update=True,
            skip_locked=True,
        )
        if not pending:
            return None

        job = pending[0]
        job.status = JobStatus.CLAIMED
        job.claimed_at = datetime.now(UTC)
        job.claimed_by = worker
        job.claim_token = uuid4()
        return await self.save(job)

    async def ensure_background(self, kind: JobKind, session_id: str, project: str, note_path: str | None = None) -> Job | None:
        now = datetime.now(UTC)
        existing = await self.find_by_session(BACKGROUND_AGENT, BACKGROUND_DEVICE, session_id, lock=True)
        if existing is not None and existing.status not in {JobStatus.DONE, JobStatus.FAILED}:
            return None
        job = existing or Job(
            agent=BACKGROUND_AGENT,
            device=BACKGROUND_DEVICE,
            session_id=session_id,
            project=project,
            project_inference=ProjectInference.REMOTE,
            created_at=now,
        )
        job.kind = kind
        job.priority = BACKGROUND_PRIORITY
        job.project = project
        job.note_path = note_path
        job.status = JobStatus.PENDING
        job.attempts = 0
        job.last_error = None
        job.next_attempt_at = None
        job.last_activity_at = now
        return await self.save(job)

    async def count_pending_background(self) -> int:
        query = select(func.count()).select_from(Job).where(col(Job.status) == JobStatus.PENDING, col(Job.priority) > 0)
        return int((await self.session.exec(query)).one())

    async def ensure_memory_merge(self, project: str, name: str, note_path: str, document: dict[str, Any]) -> Job:
        now = datetime.now(UTC)
        session_id = background_session(JobKind.MEMORY_MERGE, f"{project}/{name}")
        existing = await self.find_by_session(BACKGROUND_AGENT, BACKGROUND_DEVICE, session_id, lock=True)
        applied = existing is None or existing.status is JobStatus.DONE
        idle = existing is None or existing.status in {JobStatus.DONE, JobStatus.FAILED}
        job = existing or Job(
            agent=BACKGROUND_AGENT,
            device=BACKGROUND_DEVICE,
            session_id=session_id,
            project=project,
            project_inference=ProjectInference.REMOTE,
            created_at=now,
        )
        job.kind = JobKind.MEMORY_MERGE
        job.project = project
        job.note_path = note_path
        job.transcript = [document] if applied else [*(job.transcript or []), document]
        job.last_activity_at = now
        if idle:
            job.status = JobStatus.PENDING
            job.attempts = 0
            job.last_error = None
            job.next_attempt_at = None
            job.transcript_digest = None
        return await self.save(job)

    async def carry_memory_merges(self, source: str, target: str, base: str) -> int:
        carried = 0
        for held in await self.unfinished_memory_merges(source):
            name = PurePosixPath(held.note_path or "").name
            if not name:
                continue
            note_path = f"{base}/{name}"
            if held.session_id == background_session(JobKind.MEMORY_MERGE, f"{target}/{name}"):
                held.note_path = note_path
                await self.save(held)
                continue
            for document in held.transcript or []:
                await self.ensure_memory_merge(target, name, note_path, document)
            await self.drop(held)
            carried += 1
        return carried

    async def drop_background(self, project: str, kind: JobKind) -> int:
        result = await self.session.exec(delete(Job).where(col(Job.kind) == kind, col(Job.project) == project))
        await self.session.commit()
        return int(result.rowcount or 0)

    async def unfinished_memory_merges(self, project: str) -> Sequence[Job]:
        return await self.list(
            query_filter=and_(
                col(Job.kind) == JobKind.MEMORY_MERGE,
                col(Job.project) == project,
                col(Job.status) != JobStatus.DONE,
            ),
        )

    async def drop(self, job: Job) -> None:
        await self.session.delete(job)
        await self.session.commit()

    async def reopen(self, job: Job) -> Job:
        job.status = JobStatus.PENDING
        job.priority = BACKGROUND_PRIORITY
        job.refined_at = datetime.now(UTC)
        job.attempts = 0
        job.next_attempt_at = None
        return await self.save(job)

    async def refinable(self, summarizers: list[str], limit: int) -> Sequence[Job]:
        return await self.list(
            query_filter=and_(
                col(Job.kind) == JobKind.SESSION,
                col(Job.status) == JobStatus.DONE,
                col(Job.transcript).is_not(None),
                col(Job.refined_at).is_(None),
                col(Job.note_path).is_not(None),
                col(Job.summarizer).in_(summarizers),
            ),
            order_by=[col(Job.created_at)],
            limit=limit,
        )

    async def claim_overdue(self, deadline: timedelta, limit: int, worker: str) -> Sequence[Job]:
        overdue = await self.find_overdue(deadline, limit)
        for job in overdue:
            job.status = JobStatus.CLAIMED
            job.claimed_at = datetime.now(UTC)
            job.claimed_by = worker
            job.attempts += 1
            self.session.add(job)
        await self.session.commit()
        for job in overdue:
            await self.session.refresh(job)
        return overdue

    async def find_overdue(self, deadline: timedelta, limit: int) -> Sequence[Job]:
        return await self.list(
            query_filter=and_(
                col(Job.status) == JobStatus.PENDING,
                col(Job.created_at) < datetime.now(UTC) - deadline,
            ),
            order_by=[col(Job.created_at)],
            limit=limit,
            with_for_update=True,
            skip_locked=True,
        )

    async def reclaim_stale(self, stale_after: timedelta) -> int:
        return await self.bulk_update(
            and_(col(Job.status) == JobStatus.CLAIMED, col(Job.claimed_at) < datetime.now(UTC) - stale_after),
            status=JobStatus.PENDING,
            claimed_at=None,
            claimed_by=None,
        )

    async def purge_transcripts(self, retention: timedelta) -> int:
        return await self.bulk_update(
            and_(
                col(Job.kind) == JobKind.SESSION,
                col(Job.transcript).is_not(None),
                col(Job.last_activity_at) < datetime.now(UTC) - retention,
                col(Job.status).in_([JobStatus.DONE, JobStatus.FAILED]),
            ),
            transcript=None,
        )


jobRepositoryDI = Annotated[JobRepository, Depends(JobRepository)]  # noqa: N816
