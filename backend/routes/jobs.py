from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query, Response, status

from backend.models import JobBase, JobStatus
from backend.schemas import JobClaimed, JobFailRequest, JobPublic, MemoryContent, NoteRelations, ProjectSuggestions, SummaryResult
from backend.services.background import backgroundServiceDI
from backend.services.jobs import jobServiceDI

DEFAULT_LIST_LIMIT = 50
MAX_LIST_LIMIT = 200

router = APIRouter(prefix="/jobs", tags=["jobs"])


@router.post("")
async def enqueue(payload: JobBase, service: jobServiceDI, response: Response) -> JobPublic:
    job, created = await service.enqueue(payload)
    response.status_code = status.HTTP_202_ACCEPTED if created else status.HTTP_200_OK
    return JobPublic.model_validate(job, from_attributes=True)


@router.post("/claim", response_model=JobClaimed | None)
async def claim(
    service: jobServiceDI,
    background: backgroundServiceDI,
    worker: Annotated[str, Query(description="worker name")],
    provider: Annotated[str | None, Query(description="provider claiming for itself; decides whether background work is offered")] = None,
    min_age_seconds: Annotated[int, Query(ge=0, description="skip jobs whose last activity is more recent than this")] = 0,
) -> JobClaimed | Response:
    if (job := await service.claim_next(worker, provider, min_age_seconds)) is None:
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    return await background.claimed(job)


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


@router.post("/{job_id}/memory")
async def submit_memory(
    job_id: UUID,
    payload: MemoryContent,
    service: jobServiceDI,
    token: Annotated[UUID, Query(description="claim token returned by /claim")],
    summarizer: Annotated[str, Query(description="name of the provider that produced this")],
) -> JobPublic:
    job = await service.claim_held(job_id, token)
    return JobPublic.model_validate(await service.complete_memory(job, payload.content, summarizer), from_attributes=True)


@router.post("/{job_id}/overview")
async def submit_overview(
    job_id: UUID,
    payload: SummaryResult,
    service: jobServiceDI,
    token: Annotated[UUID, Query(description="claim token returned by /claim")],
    summarizer: Annotated[str, Query(description="name of the provider that produced this")],
) -> JobPublic:
    job = await service.claim_held(job_id, token)
    return JobPublic.model_validate(await service.complete_overview(job, payload, summarizer), from_attributes=True)


@router.post("/{job_id}/suggestions")
async def submit_suggestions(
    job_id: UUID,
    payload: ProjectSuggestions,
    service: jobServiceDI,
    token: Annotated[UUID, Query(description="claim token returned by /claim")],
    summarizer: Annotated[str, Query(description="name of the provider that produced this")],
) -> JobPublic:
    job = await service.claim_held(job_id, token)
    return JobPublic.model_validate(await service.complete_suggestions(job, payload, summarizer), from_attributes=True)


@router.post("/{job_id}/relations")
async def submit_relations(
    job_id: UUID,
    payload: NoteRelations,
    service: jobServiceDI,
    token: Annotated[UUID, Query(description="claim token returned by /claim")],
    summarizer: Annotated[str, Query(description="name of the provider that produced this")],
) -> JobPublic:
    job = await service.claim_held(job_id, token)
    return JobPublic.model_validate(await service.complete_relations(job, payload, summarizer), from_attributes=True)


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
