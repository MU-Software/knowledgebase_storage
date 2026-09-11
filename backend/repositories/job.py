from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Annotated
from uuid import uuid4

from fastapi import Depends
from sqlmodel import col, desc, select
from sqlmodel.sql.expression import and_, or_

from backend.models import Job, JobStatus
from backend.repositories import DBRepositoryImpl, OrderByType

if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import timedelta


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

    async def claim_next(self, worker: str, idle: timedelta) -> Job | None:
        now = datetime.now(UTC)
        pending = await self.list(
            query_filter=and_(
                col(Job.status) == JobStatus.PENDING,
                col(Job.last_activity_at) <= now - idle,
                or_(col(Job.next_attempt_at).is_(None), col(Job.next_attempt_at) <= now),
            ),
            order_by=[col(Job.created_at)],
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
                col(Job.transcript).is_not(None),
                col(Job.last_activity_at) < datetime.now(UTC) - retention,
                col(Job.status).in_([JobStatus.DONE, JobStatus.FAILED]),
            ),
            transcript=None,
        )


jobRepositoryDI = Annotated[JobRepository, Depends(JobRepository)]  # noqa: N816
