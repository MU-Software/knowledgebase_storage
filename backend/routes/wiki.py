from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status

from backend.dependencies.auth import require_login
from backend.repositories.suggestion import projectSuggestionRepositoryDI
from backend.schemas import (
    JobPublic,
    MemoryFile,
    MemorySync,
    MemorySyncResult,
    NoteDetail,
    NoteSummary,
    NoteWrite,
    ProjectContext,
    ProjectDeleteResult,
    ProjectMerge,
    ProjectMergeResult,
    ProjectNode,
    ProjectReparent,
    SuggestionPublic,
)
from backend.services.context import contextServiceDI
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


@owner_router.patch("/projects/{name}")
async def reparent_project(name: str, payload: ProjectReparent, service: projectServiceDI) -> ProjectNode:
    return await service.reparent(name, payload.parent)


@router.get("/suggestions")
async def suggestions(repository: projectSuggestionRepositoryDI) -> list[SuggestionPublic]:
    return [SuggestionPublic.model_validate(row, from_attributes=True) for row in await repository.list_open()]


@owner_router.post("/suggestions/{suggestion_id}/apply")
async def apply_suggestion(suggestion_id: UUID, service: projectServiceDI) -> SuggestionPublic:
    return await service.decide_suggestion(suggestion_id, applied=True)


@owner_router.post("/suggestions/{suggestion_id}/dismiss")
async def dismiss_suggestion(suggestion_id: UUID, service: projectServiceDI) -> SuggestionPublic:
    return await service.decide_suggestion(suggestion_id, applied=False)


@owner_router.post("/projects/{name}/overview", status_code=status.HTTP_202_ACCEPTED)
async def request_overview(name: str, service: projectServiceDI) -> JobPublic | None:
    job = await service.request_overview(await service.resolve(name))
    return None if job is None else JobPublic.model_validate(job, from_attributes=True)


@owner_router.delete("/projects/{name}")
async def delete_project(name: str, service: projectServiceDI) -> ProjectDeleteResult:
    return await service.delete(name)


@router.get("/notes")
async def notes(service: noteServiceDI, projects: projectServiceDI, project: Annotated[str | None, Query()] = None) -> list[NoteSummary]:
    return service.list_notes(None if project is None else await projects.resolve_path(project))


@router.get("/notes/{path:path}")
def note(path: str, service: noteServiceDI) -> NoteDetail:
    return service.retrieve(path)


@router.put("/notes", status_code=status.HTTP_201_CREATED)
def put_note(payload: NoteWrite, service: noteServiceDI) -> NoteDetail:
    return service.retrieve(service.store(payload.path, payload.content))


@router.get("/memories")
async def memories(service: noteServiceDI, projects: projectServiceDI, project: Annotated[str, Query(min_length=1)]) -> list[MemoryFile]:
    return service.list_memories(await projects.resolve_path(project))


@router.put("/memories")
async def sync_memories(payload: MemorySync, service: noteServiceDI, projects: projectServiceDI) -> MemorySyncResult:
    slug = await projects.resolve(payload.project)
    return service.sync_memories(projects.path_of(slug), payload.model_copy(update={"project": slug}))


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
