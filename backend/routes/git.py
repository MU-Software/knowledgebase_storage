from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query

from backend.schemas import GitIngestRequest, GitQuery, GitQueryResult, GitSourceState
from backend.services.git import gitServiceDI

router = APIRouter(prefix="/git", tags=["git"])


@router.get("/sources")
async def known_refs(service: gitServiceDI, device: Annotated[str, Query()], path: Annotated[str, Query()]) -> GitSourceState:
    return await service.known(device, path)


@router.post("/sources")
async def ingest_bundle(payload: GitIngestRequest, service: gitServiceDI) -> GitSourceState:
    return await service.ingest(payload)


@router.post("/query")
async def query(payload: GitQuery, service: gitServiceDI) -> GitQueryResult:
    return GitQueryResult(output=await service.query(payload))
