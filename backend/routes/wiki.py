from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query

from backend.schemas import NoteDetail, NoteSummary, ProjectSummary
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


@router.get("/search")
def search(service: noteServiceDI, q: Annotated[str, Query(min_length=1)]) -> list[NoteSummary]:
    return service.search(q)
