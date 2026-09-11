from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query, status

from backend.schemas import MemoryFile, MemorySync, MemorySyncResult, NoteDetail, NoteSummary, NoteWrite, ProjectContext, ProjectSummary
from backend.services.context import contextServiceDI
from backend.services.notes import noteServiceDI

router = APIRouter(prefix="/wiki", tags=["wiki"])


@router.get("/projects")
def projects(service: noteServiceDI) -> list[ProjectSummary]:
    return service.list_projects()


@router.get("/notes")
def notes(service: noteServiceDI, project: Annotated[str | None, Query()] = None) -> list[NoteSummary]:
    return service.list_notes(project)


@router.get("/notes/{path:path}")
def note(path: str, service: noteServiceDI) -> NoteDetail:
    return service.retrieve(path)


@router.put("/notes", status_code=status.HTTP_201_CREATED)
def put_note(payload: NoteWrite, service: noteServiceDI) -> NoteDetail:
    return service.retrieve(service.store(payload.path, payload.content))


@router.get("/memories")
def memories(service: noteServiceDI, project: Annotated[str, Query(min_length=1)]) -> list[MemoryFile]:
    return service.list_memories(project)


@router.put("/memories")
def sync_memories(payload: MemorySync, service: noteServiceDI) -> MemorySyncResult:
    return service.sync_memories(payload)


@router.get("/context")
async def context(
    service: contextServiceDI,
    project: Annotated[str, Query(min_length=1)],
    *,
    session_id: Annotated[str | None, Query(description="the session asking, left out of the unsummarized list")] = None,
    memories: Annotated[bool, Query(description="include the memory list, for agents that do not load it themselves")] = False,
) -> ProjectContext:
    return ProjectContext(context=await service.build(project, session_id, memories=memories))


@router.get("/search")
def search(service: noteServiceDI, q: Annotated[str, Query(min_length=1)]) -> list[NoteSummary]:
    return service.search(q)
