from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Body, Query, status

from backend.dependencies.transcripts import transcriptRefDI
from backend.models import UploadKind
from backend.schemas import TranscriptState, UploadState
from backend.services.ingest import ingestServiceDI

MAX_REBUILD = 500

router = APIRouter(prefix="/raw", tags=["raw"])


@router.get("/transcripts")
async def transcript_state(service: ingestServiceDI, ref: transcriptRefDI) -> TranscriptState:
    return await service.state(ref)


@router.put("/transcripts")
async def append_transcript(
    service: ingestServiceDI,
    ref: transcriptRefDI,
    offset: Annotated[int, Query(ge=0, description="bytes the server already holds; a mismatch answers 409 with the real size")],
    payload: Annotated[bytes, Body(media_type="application/x-ndjson")],
) -> TranscriptState:
    return await service.append(ref, offset, payload)


@router.post("/rebuild", status_code=status.HTTP_202_ACCEPTED)
async def rebuild(
    service: ingestServiceDI,
    device: Annotated[str | None, Query(description="only this machine's sessions")] = None,
    project: Annotated[str | None, Query(description="only sessions the hook filed under this project")] = None,
    limit: Annotated[int, Query(ge=1, le=MAX_REBUILD)] = 50,
    offset: Annotated[int, Query(ge=0, description="skip this many of the newest transcripts, to page through all of them")] = 0,
) -> list[UUID]:
    return await service.rebuild(device, project, limit, offset)


@router.post("/uploads", status_code=status.HTTP_201_CREATED)
async def start_upload(service: ingestServiceDI, kind: Annotated[UploadKind, Query()]) -> UploadState:
    return await service.start_upload(kind)


@router.put("/uploads/{upload_id}")
async def add_part(
    service: ingestServiceDI,
    upload_id: UUID,
    offset: Annotated[int, Query(ge=0)],
    payload: Annotated[bytes, Body(media_type="application/octet-stream")],
) -> UploadState:
    return await service.add_part(upload_id, offset, payload)


@router.post("/uploads/{upload_id}/complete")
async def finish_upload(
    service: ingestServiceDI,
    upload_id: UUID,
    digest: Annotated[str, Query(description="sha256 of the whole upload")] = "",
) -> UploadState:
    return await service.finish_upload(upload_id, digest)
