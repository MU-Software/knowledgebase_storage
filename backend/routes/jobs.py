from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query, Response, status

from backend.models import Job, JobBase, JobStatus
from backend.schemas import JobFailRequest, JobPublic, SummaryResult
from backend.services.jobs import jobServiceDI

DEFAULT_LIST_LIMIT = 50
MAX_LIST_LIMIT = 200

router = APIRouter(prefix="/jobs", tags=["jobs"])


@router.post("")
async def enqueue(payload: JobBase, service: jobServiceDI, response: Response) -> JobPublic:
    job, created = await service.enqueue(payload)
    response.status_code = status.HTTP_202_ACCEPTED if created else status.HTTP_200_OK
    return JobPublic.model_validate(job, from_attributes=True)


@router.post("/claim", response_model=Job | None)
async def claim(service: jobServiceDI, worker: Annotated[str, Query(description="worker name")]) -> Job | Response:
    if (job := await service.claim_next(worker)) is None:
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    return job


@router.post("/{job_id}/result")
async def submit_result(
    job_id: UUID,
    payload: SummaryResult,
    service: jobServiceDI,
    token: Annotated[UUID, Query(description="claim token returned by /claim")],
    summarizer: Annotated[str, Query(description="name of the provider that produced this")],
) -> JobPublic:
    job = await service.claim_held(job_id, token)
    return JobPublic.model_validate(await service.complete(job, payload, summarizer), from_attributes=True)


@router.post("/{job_id}/fail")
async def submit_failure(
    job_id: UUID,
    payload: JobFailRequest,
    service: jobServiceDI,
    token: Annotated[UUID, Query(description="claim token returned by /claim")],
) -> JobPublic:
    job = await service.claim_held(job_id, token)
    return JobPublic.model_validate(await service.fail(job, payload.error), from_attributes=True)


@router.post("/{job_id}/release")
async def release(
    job_id: UUID,
    service: jobServiceDI,
    token: Annotated[UUID, Query(description="claim token returned by /claim")],
    retry_after: Annotated[int, Query(ge=0, description="seconds to wait before this job is claimable again")] = 0,
) -> JobPublic:
    job = await service.claim_held(job_id, token)
    return JobPublic.model_validate(await service.release(job, retry_after), from_attributes=True)


@router.get("")
async def list_jobs(
    service: jobServiceDI,
    job_status: Annotated[JobStatus | None, Query(alias="status")] = None,
    limit: Annotated[int, Query(le=MAX_LIST_LIMIT)] = DEFAULT_LIST_LIMIT,
) -> list[JobPublic]:
    jobs = await service.list_jobs(job_status, limit)
    return [JobPublic.model_validate(job, from_attributes=True) for job in jobs]
