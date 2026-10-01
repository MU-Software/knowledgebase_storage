from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status

from backend.dependencies.auth import require_login
from backend.schemas import (
    JobPublic,
    MemoryFile,
    MemorySync,
    MemorySyncResult,
    NoteDecisionPublic,
    NoteDetail,
    NoteForget,
    NoteForgotten,
    NoteMove,
    NoteMoved,
    NotePinPublic,
    NoteSummary,
    NoteWrite,
    ProjectContext,
    ProjectDeleteResult,
    ProjectEntryPublic,
    ProjectEntryWrite,
    ProjectLinkPublic,
    ProjectLinkWrite,
    ProjectMerge,
    ProjectMergeResult,
    ProjectNode,
)
from backend.services.context import contextServiceDI
from backend.services.decisions import decisionServiceDI
from backend.services.entries import entryServiceDI
from backend.services.links import linkServiceDI
from backend.services.notes import noteServiceDI
from backend.services.projects import projectServiceDI

router = APIRouter(prefix="/wiki", tags=["wiki"])
owner_router = APIRouter(prefix="/wiki", tags=["wiki"], dependencies=[Depends(require_login)])


@router.get("/projects")
async def projects(service: projectServiceDI) -> list[ProjectNode]:
    return await service.tree()


@owner_router.post("/projects/merge")
async def merge_projects(payload: ProjectMerge, service: projectServiceDI) -> ProjectMergeResult:
    return await service.merge(payload)


@router.get("/entries")
async def entries(service: entryServiceDI) -> list[ProjectEntryPublic]:
    found = await service.listed()
    return [ProjectEntryPublic.model_validate(entry, from_attributes=True) for entry in found]


@owner_router.put("/entries")
async def write_entry(payload: ProjectEntryWrite, service: entryServiceDI) -> ProjectEntryPublic:
    return await service.write(payload)


@router.get("/links")
async def links(service: linkServiceDI, project: Annotated[str | None, Query()] = None) -> list[ProjectLinkPublic]:
    found = await service.listed(project)
    return [ProjectLinkPublic.model_validate(link, from_attributes=True) for link in found]


@owner_router.put("/links")
async def write_link(payload: ProjectLinkWrite, service: linkServiceDI) -> ProjectLinkPublic:
    return await service.write(payload)


@owner_router.post("/links/apply")
async def apply_link(
    service: linkServiceDI,
    source: Annotated[str, Query()],
    target: Annotated[str, Query()],
    *,
    reverse: Annotated[bool, Query(description="merge or file the other way round")] = False,
) -> ProjectLinkPublic:
    return await service.apply(source, target, reverse=reverse)


@owner_router.delete("/links", status_code=status.HTTP_204_NO_CONTENT)
async def drop_link(service: linkServiceDI, source: Annotated[str, Query()], target: Annotated[str, Query()]) -> None:
    await service.drop(source, target)


@owner_router.post("/projects/{name}/overview", status_code=status.HTTP_202_ACCEPTED)
async def request_overview(name: str, service: projectServiceDI) -> JobPublic | None:
    job = await service.request_overview(name)
    return None if job is None else JobPublic.model_validate(job, from_attributes=True)


@owner_router.delete("/projects/{name}")
async def delete_project(name: str, service: projectServiceDI) -> ProjectDeleteResult:
    return await service.delete(name)


@owner_router.post("/notes/forget")
async def forget_notes(payload: NoteForget, service: decisionServiceDI) -> list[NoteForgotten]:
    return await service.forget(payload)


@owner_router.post("/notes/move")
async def move_note(payload: NoteMove, service: decisionServiceDI, projects: projectServiceDI) -> NoteMoved:
    return await service.move(payload, await projects.resolve(payload.project))


@router.get("/pins")
async def pins(service: decisionServiceDI) -> list[NotePinPublic]:
    return [NotePinPublic.model_validate(row, from_attributes=True) for row in await service.pinned()]


@owner_router.delete("/pins/{pin_id}", status_code=status.HTTP_204_NO_CONTENT)
async def unpin_note(pin_id: UUID, service: decisionServiceDI) -> None:
    await service.unpin(pin_id)


@router.get("/decisions")
async def decisions(service: decisionServiceDI) -> list[NoteDecisionPublic]:
    return [NoteDecisionPublic.model_validate(row, from_attributes=True) for row in await service.listed()]


@owner_router.delete("/decisions/{decision_id}", status_code=status.HTTP_204_NO_CONTENT)
async def allow_note(decision_id: UUID, service: decisionServiceDI) -> None:
    await service.allow(decision_id)


@router.get("/notes")
async def notes(service: noteServiceDI, projects: projectServiceDI, project: Annotated[str | None, Query()] = None) -> list[NoteSummary]:
    return service.list_notes(None if project is None else await projects.resolve(project))


@router.get("/notes/{path:path}")
def note(path: str, service: noteServiceDI) -> NoteDetail:
    return service.retrieve(path)


@router.put("/notes", status_code=status.HTTP_201_CREATED)
def put_note(payload: NoteWrite, service: noteServiceDI) -> NoteDetail:
    return service.retrieve(service.store(payload.path, payload.content))


@router.get("/memories")
async def memories(service: noteServiceDI, projects: projectServiceDI, project: Annotated[str, Query(min_length=1)]) -> list[MemoryFile]:
    return service.list_memories(await projects.resolve(project))


@router.put("/memories")
async def sync_memories(payload: MemorySync, service: noteServiceDI, projects: projectServiceDI) -> MemorySyncResult:
    slug = await projects.resolve(payload.project)
    return service.sync_memories(slug, payload.model_copy(update={"project": slug}))


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
