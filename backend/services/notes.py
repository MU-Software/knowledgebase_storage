from __future__ import annotations

import re
from collections import Counter
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Annotated

import frontmatter
from fastapi import Depends

from backend.repositories.note import NoteRepository, noteRepositoryDI
from backend.schemas import ProjectSummary
from backend.services import ServiceImpl

if TYPE_CHECKING:
    from backend.models import Job
    from backend.schemas import NoteDetail, NoteSummary, SummaryResult

SEARCH_RESULT_LIMIT = 50
_UNSAFE = re.compile(r"[^\w.-]+", re.UNICODE)


def slugify(value: str) -> str:
    return _UNSAFE.sub("-", value.strip()).strip("-").lower() or "unknown"


class NoteService(ServiceImpl[NoteRepository]):
    repository: noteRepositoryDI

    @staticmethod
    def render(job: Job, result: SummaryResult, summarizer: str) -> str:
        post = frontmatter.Post(content="")
        post.metadata = {
            "title": result.title,
            "type": "note",
            "tags": [f"project/{job.project}", f"agent/{job.agent}", f"device/{job.device}", *result.tags],
            "source": {
                "agent": job.agent,
                "model": job.model,
                "device": job.device,
                "session_id": job.session_id,
                "cwd": job.cwd,
                "project_inference": job.project_inference.value,
                "summarizer": summarizer,
                "ingested_at": datetime.now(UTC).isoformat(),
            },
        }

        lines = [f"# {result.title}", "", result.summary, ""]
        if result.observations:
            lines += ["## Observations", ""]
            lines += [f"- [{o.category}] {o.text}" + "".join(f" #{t}" for t in o.tags) for o in result.observations]
            lines.append("")
        if result.relations:
            lines += ["## Relations", ""]
            lines += [f"- {r.type} [[{r.target}]]" for r in result.relations]
            lines.append("")

        post.content = "\n".join(lines).rstrip() + "\n"
        return frontmatter.dumps(post)

    def write(self, job: Job, result: SummaryResult, summarizer: str) -> str:
        day = (job.ended_at or job.created_at).strftime("%Y-%m-%d")
        relative = f"projects/{slugify(job.project)}/log/{day}-{slugify(job.agent)}-{job.id}.md"
        return self.repository.write(relative, self.render(job, result, summarizer).rstrip() + "\n")

    def store(self, relative_path: str, content: str) -> str:
        return self.repository.write(relative_path, content.rstrip() + "\n")

    def list_projects(self) -> list[ProjectSummary]:
        summaries = self.repository.list_summaries()
        counts = Counter(summary.project for summary in summaries)
        unconfirmed = {s.project for s in summaries if s.source.get("project_inference", "remote") != "remote"}
        return [ProjectSummary(name=name, note_count=count, unconfirmed=name in unconfirmed) for name, count in sorted(counts.items())]

    def list_notes(self, project: str | None = None) -> list[NoteSummary]:
        return self.repository.list_summaries(project)

    def retrieve(self, relative_path: str) -> NoteDetail:
        return self.repository.retrieve(relative_path)

    def search(self, query: str, limit: int = SEARCH_RESULT_LIMIT) -> list[NoteSummary]:
        return self.repository.grep(query.lower(), limit)


noteServiceDI = Annotated[NoteService, Depends(NoteService)]  # noqa: N816
