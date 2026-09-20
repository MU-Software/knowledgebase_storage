from __future__ import annotations

from pathlib import PurePosixPath
from typing import TYPE_CHECKING, Annotated

from fastapi import Depends

from backend.consts.notes import ancestors_of
from backend.repositories.job import JobRepository, jobRepositoryDI
from backend.services import ServiceImpl
from backend.services.notes import noteServiceDI
from backend.services.projects import leaf_of, projectServiceDI

if TYPE_CHECKING:
    from backend.models import Job

RECENT_LOG_LIMIT = 5
ANCESTOR_LOG_LIMIT = 3
UNSUMMARIZED_LIMIT = 3
OBSERVATIONS_PER_LOG = 3
TEXT_CHARS = 300
KEPT_CATEGORIES = ("next", "problem", "decision")
ANCESTOR_HEADING = "## The projects this one sits under"


def clip(text: str, limit: int = TEXT_CHARS) -> str:
    flat = " ".join(text.split())
    return flat if len(flat) <= limit else flat[: limit - 1] + "…"


class ContextService(ServiceImpl[JobRepository]):
    repository: jobRepositoryDI
    notes: noteServiceDI
    projects: projectServiceDI

    async def build(self, project: str, session_id: str | None, *, memories: bool) -> str:
        slug = await self.projects.resolve(project)
        path = self.projects.path_of(slug)
        sections = [self.log_lines(path), self.ancestor_lines(path), await self.unsummarized_lines(slug, session_id)]
        if memories:
            sections.append(self.memory_lines(path))
        if not any(sections):
            return ""

        header = [
            f"# kb-storage: {slug}",
            "",
            f"What other devices and sessions recorded for this project. The notes live under `projects/{path}/` in basic-memory.",
        ]
        return "\n".join([*header, *(line for section in sections for line in section)]) + "\n"

    def digest(self, project_path: str, limit: int, *, subtree: bool) -> list[str]:
        lines: list[str] = []
        for note in self.notes.recent_logs(project_path, limit, subtree=subtree):
            summary, observations = self.notes.parse_log(note.content)
            day = PurePosixPath(note.path).name[:10]
            where = "" if note.project_path == project_path else f" [{note.project_path}]"
            lines.append(f"- {day} {note.source.get('agent')}@{note.source.get('device')}{where}: {note.title} (`{note.path}`)")
            if summary:
                lines.append(f"  {clip(summary)}")
            kept = sorted((o for o in observations if o.category in KEPT_CATEGORIES), key=lambda o: KEPT_CATEGORIES.index(o.category))
            lines += [f"  - [{o.category}] {clip(o.text)}" for o in kept[:OBSERVATIONS_PER_LOG]]
        return lines

    def log_lines(self, project_path: str) -> list[str]:
        lines = self.digest(project_path, RECENT_LOG_LIMIT, subtree=True)
        return ["", "## Recent sessions", *lines] if lines else []

    def ancestor_lines(self, project_path: str) -> list[str]:
        lines: list[str] = []
        for ancestor in ancestors_of(project_path):
            if entries := self.digest(ancestor, ANCESTOR_LOG_LIMIT, subtree=False):
                lines += [f"### {leaf_of(ancestor)}", *entries]
        return ["", ANCESTOR_HEADING, *lines] if lines else []

    def ancestor_digest(self, project_path: str) -> str:
        lines = self.ancestor_lines(project_path)
        return "\n".join(lines).strip() if lines else ""

    async def for_job(self, job: Job) -> str:
        return self.ancestor_digest(await self.projects.resolve_path(job.project))

    async def unsummarized_lines(self, project: str, session_id: str | None) -> list[str]:
        jobs = await self.repository.list_unsummarized(project, session_id, UNSUMMARIZED_LIMIT)
        lines = [f"- {job.last_activity_at.astimezone():%Y-%m-%d %H:%M} {job.agent}@{job.device}: {clip(job.last_request or '')}" for job in jobs]
        return ["", "## Last requests of sessions not summarized yet", *lines] if lines else []

    def memory_lines(self, project_path: str) -> list[str]:
        lines = []
        for name, description in self.notes.memory_descriptions(project_path):
            title = name.removesuffix(".md")
            lines.append(f"- {title}: {clip(description)}" if description else f"- {title}")
        return ["", "## Memories", *lines] if lines else []


contextServiceDI = Annotated[ContextService, Depends(ContextService)]  # noqa: N816
