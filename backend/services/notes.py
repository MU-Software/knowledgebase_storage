from __future__ import annotations

import logging
import re
from collections import Counter
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Annotated, get_args

import frontmatter
from fastapi import Depends

from backend.repositories.note import NoteRepository, noteRepositoryDI
from backend.schemas import MemoryFile, MemorySyncResult, Observation, ObservationCategory, ProjectSummary
from backend.services import ServiceImpl

if TYPE_CHECKING:
    from backend.models import Job
    from backend.schemas import MemorySync, NoteDetail, NoteSummary, SummaryResult

logger = logging.getLogger(__name__)

SEARCH_RESULT_LIMIT = 50
MEMORY_NOTE_KEYS = frozenset({"title", "type", "tags", "source", "permalink"})
OBSERVATIONS_HEADING = "## Observations"
RELATIONS_HEADING = "## Relations"
_UNSAFE = re.compile(r"[^\w.-]+", re.UNICODE)
_OBSERVATION = re.compile(rf"^- \[(?P<category>{'|'.join(get_args(ObservationCategory))})\] (?P<text>.*?)(?P<tags>(?: #[^\s#]+)*)$")


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
            lines += [OBSERVATIONS_HEADING, ""]
            lines += [f"- [{o.category}] {o.text}" + "".join(f" #{t}" for t in o.tags) for o in result.observations]
            lines.append("")
        if result.relations:
            lines += [RELATIONS_HEADING, ""]
            lines += [f"- {r.type} [[{r.target}]]" for r in result.relations]
            lines.append("")

        post.content = "\n".join(lines).rstrip() + "\n"
        return frontmatter.dumps(post)

    @staticmethod
    def parse_log(content: str) -> tuple[str, list[Observation]]:
        summary: list[str] = []
        observations: list[Observation] = []
        section: str | None = None
        for line in content.splitlines():
            if line.startswith(("# ", "## ")):
                section = line
            elif section is not None and not section.startswith("## "):
                summary.append(line)
            elif section == OBSERVATIONS_HEADING and (match := _OBSERVATION.match(line)):
                tags = [tag.removeprefix("#") for tag in match["tags"].split()]
                observations.append(Observation.model_validate({"category": match["category"], "text": match["text"], "tags": tags}))
        return "\n".join(summary).strip(), observations

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

    @staticmethod
    def description_of(text: str) -> str:
        try:
            return str(frontmatter.loads(text).metadata.get("description") or "")
        except Exception:
            logger.warning("skipping the description of a memory note with unreadable frontmatter", exc_info=True)
            return ""

    def write(self, job: Job, result: SummaryResult, summarizer: str) -> str:
        day = (job.ended_at or job.created_at).strftime("%Y-%m-%d")
        relative = job.note_path or f"projects/{slugify(job.project)}/log/{day}-{slugify(job.agent)}-{job.id}.md"
        return self.repository.write(relative, self.render(job, result, summarizer).rstrip() + "\n")

    def store(self, relative_path: str, content: str) -> str:
        return self.repository.write(relative_path, content.rstrip() + "\n")

    def list_memories(self, project: str) -> list[MemoryFile]:
        texts = self.repository.read_directory(f"projects/{slugify(project)}/memory")
        return [MemoryFile(name=name, content=self.restore_memory(text)) for name, text in texts.items()]

    def memory_descriptions(self, project: str) -> list[tuple[str, str]]:
        texts = self.repository.read_directory(f"projects/{slugify(project)}/memory")
        return [(name, self.description_of(text)) for name, text in texts.items() if name != "MEMORY.md"]

    def sync_memories(self, payload: MemorySync) -> MemorySyncResult:
        base = f"projects/{slugify(payload.project)}/memory"
        written = [self.repository.write(f"{base}/{file.name}", self.render_memory(payload, file)) for file in payload.files]
        deleted = [path for path in (f"{base}/{name}" for name in payload.deleted) if self.repository.delete(path)]
        return MemorySyncResult(written=written, deleted=deleted)

    def recent_logs(self, project: str, limit: int) -> list[NoteDetail]:
        logs = [summary for summary in self.repository.list_summaries(slugify(project)) if "/log/" in summary.path]
        logs.sort(key=lambda summary: str(summary.source.get("ingested_at", "")), reverse=True)
        return [self.repository.retrieve(summary.path) for summary in logs[:limit]]

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
