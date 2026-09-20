from __future__ import annotations

import logging
import re
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Annotated, get_args

import frontmatter
from fastapi import Depends

from backend.consts.notes import LOG_DIR, MEMORY_DIR, OVERVIEW_FILE, PROJECTS_ROOT, slugify
from backend.repositories.note import NoteRepository, noteRepositoryDI
from backend.schemas import MemoryFile, MemorySyncResult, NoteDetail, Observation, ObservationCategory
from backend.services import ServiceImpl

if TYPE_CHECKING:
    from backend.models import Job
    from backend.schemas import MemorySync, NoteSummary, Relation, SummaryResult

logger = logging.getLogger(__name__)

SEARCH_RESULT_LIMIT = 50
MEMORY_NOTE_KEYS = frozenset({"title", "type", "tags", "source", "permalink"})
OBSERVATIONS_HEADING = "## Observations"
RELATIONS_HEADING = "## Relations"
PROJECT_TAG_PREFIX = "project/"
_MEMORY_MARKER = re.compile(r"^\[from [^\]]+\]\s*$\n?", re.MULTILINE)
_TRAILING_TAGS = re.compile(r"(?:\s+#[^\s#]+)+$")
_FENCE = "```"
_OBSERVATION = re.compile(rf"^- \[(?P<category>{'|'.join(get_args(ObservationCategory))})\] (?P<text>.*?)(?P<tags>(?: #[^\s#]+)*)$")


class NoteService(ServiceImpl[NoteRepository]):
    repository: noteRepositoryDI

    @staticmethod
    def clean_tags(tags: list[str]) -> list[str]:
        stripped = (str(tag).strip().lstrip("#").strip() for tag in tags)
        return list(dict.fromkeys(tag for tag in stripped if tag))

    @classmethod
    def observation_line(cls, observation: Observation) -> str:
        text = observation.text.strip()
        inline = _TRAILING_TAGS.search(text)
        if inline is not None:
            text = text[: inline.start()].strip()
        tags = cls.clean_tags([*(inline.group().split() if inline else []), *observation.tags])
        return f"- [{observation.category}] {text}" + "".join(f" #{tag}" for tag in tags)

    @classmethod
    def body_of(cls, result: SummaryResult) -> str:
        lines = [f"# {result.title}", "", result.summary, ""]
        if result.observations:
            lines += [OBSERVATIONS_HEADING, ""]
            lines += [cls.observation_line(observation) for observation in result.observations]
            lines.append("")
        linked = [r for r in result.relations if r.target.strip() and r.target.strip() != result.title.strip()]
        if linked:
            lines += [RELATIONS_HEADING, ""]
            lines += [f"- {r.type} [[{r.target.strip()}]]" for r in linked]
            lines.append("")
        return "\n".join(lines).rstrip() + "\n"

    @staticmethod
    def render(job: Job, result: SummaryResult, summarizer: str) -> str:
        post = frontmatter.Post(content="")
        post.metadata = {
            "title": result.title,
            "type": "note",
            "tags": [f"project/{job.project}", f"agent/{job.agent}", f"device/{job.device}", *NoteService.clean_tags(result.tags)],
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

        post.content = NoteService.body_of(result)
        return frontmatter.dumps(post)

    @staticmethod
    def render_overview(project: str, result: SummaryResult, summarizer: str, *, logs: int, children: list[str]) -> str:
        post = frontmatter.Post(content="")
        post.metadata = {
            "title": result.title,
            "type": "note",
            "tags": [f"project/{project}", "kind/overview", *NoteService.clean_tags(result.tags)],
            "source": {
                "kind": "overview",
                "project": project,
                "logs": logs,
                "children": children,
                "summarizer": summarizer,
                "generated_at": datetime.now(UTC).isoformat(),
            },
        }
        post.content = NoteService.body_of(result)
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

    def write(self, job: Job, result: SummaryResult, summarizer: str, project_path: str) -> str:
        day = (job.ended_at or job.created_at).strftime("%Y-%m-%d")
        default = f"{PROJECTS_ROOT}/{project_path}/{LOG_DIR}/{day}-{slugify(job.agent)}-{job.id}.md"
        return self.repository.write(job.note_path or default, self.render(job, result, summarizer).rstrip() + "\n")

    def retag(self, relative_path: str, project: str) -> None:
        target = self.repository.resolve(relative_path)
        if target is None:
            return
        try:
            post = frontmatter.load(target)
        except Exception:
            logger.warning("leaving the tags of %s alone: its frontmatter is unreadable", relative_path, exc_info=True)
            return

        tags = post.metadata.get("tags")
        if not isinstance(tags, list):
            return
        renamed = [f"{PROJECT_TAG_PREFIX}{project}" if str(tag).startswith(PROJECT_TAG_PREFIX) else str(tag) for tag in tags]
        post.metadata["tags"] = list(dict.fromkeys(renamed))
        self.repository.write(relative_path, frontmatter.dumps(post).rstrip() + "\n")

    def apply_relations(self, relative_path: str, relations: list[Relation], summarizer: str) -> int:
        target = self.repository.resolve(relative_path)
        if target is None:
            return 0
        try:
            post = frontmatter.load(target)
        except Exception:
            logger.warning("leaving %s without relations: its frontmatter is unreadable", relative_path, exc_info=True)
            return 0

        content = post.content.rstrip()
        known = {*re.findall(r"\[\[(.+?)\]\]", content), str(post.metadata.get("title") or "")}
        fresh = [relation for relation in relations if relation.target and relation.target not in known]
        if fresh:
            if RELATIONS_HEADING not in content:
                content += f"\n\n{RELATIONS_HEADING}\n"
            content += "\n" + "\n".join(f"- {relation.type} [[{relation.target}]]" for relation in fresh)

        source = post.metadata.get("source")
        post.metadata["source"] = {
            **(source if isinstance(source, dict) else {}),
            "relations_at": datetime.now(UTC).isoformat(),
            "relations_by": summarizer,
        }
        post.content = content.rstrip() + "\n"
        self.repository.write(relative_path, frontmatter.dumps(post).rstrip() + "\n")
        return len(fresh)

    @staticmethod
    def overview_path(project_path: str) -> str:
        return f"{PROJECTS_ROOT}/{project_path}/{OVERVIEW_FILE}"

    def overview(self, project_path: str) -> NoteDetail | None:
        path = self.overview_path(project_path)
        return self.repository.retrieve(path) if self.repository.resolve(path) is not None else None

    def forget_overview(self, project_path: str) -> bool:
        return self.repository.delete(self.overview_path(project_path))

    def store(self, relative_path: str, content: str) -> str:
        return self.repository.write(relative_path, content.rstrip() + "\n")

    def memory_document(self, project: str, stored: str) -> dict[str, str]:
        return {"role": "user", "content": f"[from {project}]\n\n{self.restore_memory(stored).strip()}"}

    @staticmethod
    def clean_memory(text: str) -> str:
        body = text.strip()
        lines = body.splitlines()
        if body.startswith(_FENCE) and len(lines) > 1 and lines[-1].strip() == _FENCE:
            body = "\n".join(lines[1:-1])
        return _MEMORY_MARKER.sub("", body).strip() + "\n"

    @staticmethod
    def memory_dir(project_path: str) -> str:
        return f"{PROJECTS_ROOT}/{project_path}/{MEMORY_DIR}"

    def list_memories(self, project_path: str) -> list[MemoryFile]:
        texts = self.repository.read_directory(self.memory_dir(project_path))
        return [MemoryFile(name=name, content=self.restore_memory(text)) for name, text in texts.items()]

    def memory_descriptions(self, project_path: str) -> list[tuple[str, str]]:
        texts = self.repository.read_directory(self.memory_dir(project_path))
        return [(name, self.description_of(text)) for name, text in texts.items() if name != "MEMORY.md"]

    def sync_memories(self, project_path: str, payload: MemorySync) -> MemorySyncResult:
        base = self.memory_dir(project_path)
        written = [self.repository.write(f"{base}/{file.name}", self.render_memory(payload, file)) for file in payload.files]
        deleted = [path for path in (f"{base}/{name}" for name in payload.deleted) if self.repository.delete(path)]
        return MemorySyncResult(written=written, deleted=deleted)

    def recent_logs(self, project_path: str, limit: int, *, subtree: bool = True) -> list[NoteDetail]:
        logs = [summary for summary in self.repository.list_summaries(project_path) if f"/{LOG_DIR}/" in summary.path]
        if not subtree:
            logs = [summary for summary in logs if summary.project_path == project_path]
        logs.sort(key=lambda summary: str(summary.source.get("ingested_at", "")), reverse=True)
        return [self.repository.retrieve(summary.path) for summary in logs[:limit]]

    def list_notes(self, project_path: str | None = None) -> list[NoteSummary]:
        return self.repository.list_summaries(project_path)

    def retrieve(self, relative_path: str) -> NoteDetail:
        return self.repository.retrieve(relative_path)

    def search(self, query: str, limit: int = SEARCH_RESULT_LIMIT) -> list[NoteSummary]:
        return self.repository.grep(query.lower(), limit)


noteServiceDI = Annotated[NoteService, Depends(NoteService)]  # noqa: N816
