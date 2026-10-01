from __future__ import annotations

from pathlib import PurePosixPath
from typing import TYPE_CHECKING, Annotated

from fastapi import Depends

from backend.repositories.job import JobRepository, jobRepositoryDI
from backend.repositories.setting import runtimeSettingRepositoryDI
from backend.services import ServiceImpl
from backend.services.links import linkServiceDI
from backend.services.notes import noteServiceDI
from backend.services.projects import projectServiceDI

if TYPE_CHECKING:
    from backend.models import Job

RECENT_LOG_LIMIT = 5
LINKED_LOG_LIMIT = 3
UNSUMMARIZED_LIMIT = 3
OBSERVATIONS_PER_LOG = 3
TEXT_CHARS = 300
KEPT_CATEGORIES = ("next", "problem", "decision")
LINKED_HEADING = "## The projects this one is linked to"


def clip(text: str, limit: int = TEXT_CHARS) -> str:
    flat = " ".join(text.split())
    return flat if len(flat) <= limit else flat[: limit - 1] + "…"


class ContextService(ServiceImpl[JobRepository]):
    repository: jobRepositoryDI
    notes: noteServiceDI
    projects: projectServiceDI
    links: linkServiceDI
    runtime: runtimeSettingRepositoryDI

    async def build(self, project: str, session_id: str | None, *, memories: bool) -> str:
        slug = await self.projects.resolve(project)
        sections = [self.log_lines(slug), await self.linked_lines(slug), await self.unsummarized_lines(slug, session_id)]
        if memories:
            sections.append(self.memory_lines(slug))
        if not any(sections):
            return ""

        header = [
            f"# kb-storage: {slug}",
            "",
            f"What other devices and sessions recorded for this project. The notes live under `projects/{slug}/` in basic-memory.",
        ]
        return "\n".join([*header, *(line for section in sections for line in section)]) + "\n"

    def digest(self, project: str, limit: int) -> list[str]:
        lines: list[str] = []
        for note in self.notes.recent_logs(project, limit):
            summary, observations = self.notes.parse_log(note.content)
            day = PurePosixPath(note.path).name[:10]
            lines.append(f"- {day} {note.source.get('agent')}@{note.source.get('device')}: {note.title} (`{note.path}`)")
            if summary:
                lines.append(f"  {clip(summary)}")
            kept = sorted((o for o in observations if o.category in KEPT_CATEGORIES), key=lambda o: KEPT_CATEGORIES.index(o.category))
            lines += [f"  - [{o.category}] {clip(o.text)}" for o in kept[:OBSERVATIONS_PER_LOG]]
        return lines

    def log_lines(self, project: str) -> list[str]:
        lines = self.digest(project, RECENT_LOG_LIMIT)
        return ["", "## Recent sessions", *lines] if lines else []

    async def linked_lines(self, project: str) -> list[str]:
        lines: list[str] = await self.links.neighbours(project)
        for other in await self.links.linked(project):
            if entries := self.digest(other, LINKED_LOG_LIMIT):
                lines += [f"### {other}", *entries]
        return ["", LINKED_HEADING, *lines] if lines else []

    async def for_job(self, job: Job) -> str:
        lines = await self.linked_lines(await self.projects.resolve(job.project))
        return "\n".join(lines).strip()

    async def unsummarized_lines(self, project: str, session_id: str | None) -> list[str]:
        jobs = await self.repository.list_unsummarized(project, session_id, UNSUMMARIZED_LIMIT)
        zone = (await self.runtime.get()).zone
        lines = [f"- {job.last_activity_at.astimezone(zone):%Y-%m-%d %H:%M} {job.agent}@{job.device}: {clip(job.last_request or '')}" for job in jobs]
        return ["", "## Last requests of sessions not summarized yet", *lines] if lines else []

    def memory_lines(self, project: str) -> list[str]:
        lines = []
        for name, description in self.notes.memory_descriptions(project):
            title = name.removesuffix(".md")
            lines.append(f"- {title}: {clip(description)}" if description else f"- {title}")
        return ["", "## Memories", *lines] if lines else []


contextServiceDI = Annotated[ContextService, Depends(ContextService)]  # noqa: N816
