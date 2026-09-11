from __future__ import annotations

import logging
import re
from collections import Counter
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Annotated

import frontmatter
from fastapi import Depends

from backend.repositories.note import NoteRepository, noteRepositoryDI
from backend.schemas import MemoryFile, MemorySyncResult, ProjectSummary
from backend.services import ServiceImpl

if TYPE_CHECKING:
    from backend.models import Job
    from backend.schemas import MemorySync, NoteDetail, NoteSummary, SummaryResult

logger = logging.getLogger(__name__)

SEARCH_RESULT_LIMIT = 50
MEMORY_NOTE_KEYS = frozenset({"title", "type", "tags", "source", "permalink"})
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

    @staticmethod
    def render_memory(payload: MemorySync, file: MemoryFile) -> str:
        try:
            post = frontmatter.loads(file.content)
        except Exception:
            logger.warning("keeping memory %s without its unreadable frontmatter", file.name, exc_info=True)
            post = frontmatter.Post(content=file.content)

        nested = post.metadata.get("metadata")
        kind = nested.get("type") if isinstance(nested, dict) else None
        post.metadata = {
            **post.metadata,
            "title": post.metadata.get("name") or file.name.removesuffix(".md"),
            "type": "note",
            "tags": [f"project/{payload.project}", f"agent/{payload.agent}", f"device/{payload.device}", f"type/{kind or 'memory'}"],
            "source": {"agent": payload.agent, "device": payload.device, "memory": file.name},
        }
        return frontmatter.dumps(post).rstrip() + "\n"

    @staticmethod
    def restore_memory(text: str) -> str:
        try:
            post = frontmatter.loads(text)
        except Exception:
            logger.warning("returning a memory note with unreadable frontmatter as it is", exc_info=True)
            return text

        metadata = {key: value for key, value in post.metadata.items() if key not in MEMORY_NOTE_KEYS}
        if not metadata:
            return post.content.rstrip() + "\n"
        post.metadata = metadata
        return frontmatter.dumps(post).rstrip() + "\n"

    def write(self, job: Job, result: SummaryResult, summarizer: str) -> str:
        day = (job.ended_at or job.created_at).strftime("%Y-%m-%d")
        relative = job.note_path or f"projects/{slugify(job.project)}/log/{day}-{slugify(job.agent)}-{job.id}.md"
        return self.repository.write(relative, self.render(job, result, summarizer).rstrip() + "\n")

    def store(self, relative_path: str, content: str) -> str:
        return self.repository.write(relative_path, content.rstrip() + "\n")

    def list_memories(self, project: str) -> list[MemoryFile]:
        texts = self.repository.read_directory(f"projects/{slugify(project)}/memory")
        return [MemoryFile(name=name, content=self.restore_memory(text)) for name, text in texts.items()]

    def sync_memories(self, payload: MemorySync) -> MemorySyncResult:
        base = f"projects/{slugify(payload.project)}/memory"
        written = [self.repository.write(f"{base}/{file.name}", self.render_memory(payload, file)) for file in payload.files]
        deleted = [path for path in (f"{base}/{name}" for name in payload.deleted) if self.repository.delete(path)]
        return MemorySyncResult(written=written, deleted=deleted)

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
