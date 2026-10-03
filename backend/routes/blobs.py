from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Body, Path, Query

from backend.schemas import DIGEST_PATTERN, BlobManifestPart, BlobMissingRequest, BlobMissingResult, BlobState, ChunkState
from backend.services.blobs import blobServiceDI

router = APIRouter(prefix="/blobs", tags=["blobs"])

DigestPath = Annotated[str, Path(pattern=DIGEST_PATTERN)]


@router.post("/missing")
async def missing_chunks(payload: BlobMissingRequest, service: blobServiceDI) -> BlobMissingResult:
    return BlobMissingResult(missing=await service.missing(payload.digests))


@router.put("/chunks/{digest}")
async def put_chunk(service: blobServiceDI, digest: DigestPath, payload: Annotated[bytes, Body(media_type="application/octet-stream")]) -> ChunkState:
    return ChunkState(digest=digest, byte_size=await service.put_chunk(digest, payload))


@router.get("/{digest}")
async def blob_state(service: blobServiceDI, digest: DigestPath) -> BlobState:
    return service.state_of(await service.completed(digest))


@router.post("/{digest}/manifest")
async def add_manifest(
    service: blobServiceDI,
    digest: DigestPath,
    offset: Annotated[int, Query(ge=0, description="chunks the server already holds; 0 starts the list over")],
    payload: BlobManifestPart,
) -> BlobState:
    return await service.add_manifest(digest, offset, payload)


@router.post("/{digest}/complete")
async def complete_blob(service: blobServiceDI, digest: DigestPath) -> BlobState:
    return await service.complete(digest)
