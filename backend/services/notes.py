from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import UTC, datetime, tzinfo
from typing import TYPE_CHECKING, Annotated, get_args

import frontmatter
from fastapi import Depends

from backend.consts.notes import LOG_DIR, MEMORY_DIR, OVERVIEW_FILE, PROJECTS_ROOT, slugify
from backend.repositories.note import NoteRepository, noteRepositoryDI
from backend.schemas import MemoryFile, MemorySyncResult, NoteDetail, Observation, ObservationCategory, SessionNote
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


@dataclass
class NoteTarget:
    project: str
    window: tuple[datetime | None, datetime | None]
    zone: tzinfo = UTC
    sidecar: bool = False


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
    def dated(title: str, started: datetime | None, ended: datetime | None, zone: tzinfo) -> str:
        if started is None and ended is None:
            return title
        first = (started or ended or datetime.now(UTC)).astimezone(zone)
        last = (ended or started or first).astimezone(zone)
        return f"[{first:%Y-%m-%d} - {last:%Y-%m-%d}] {title}"

    @classmethod
    def body_of_session(cls, note: SessionNote) -> str:
        lines = [f"# {note.header.title}", "", note.header.summary, "", "## 흐름", ""]
        lines += [f"{number}. {item.request} → {item.outcome}" for number, item in enumerate(note.items, 1)]
        lines += ["", "## Observations"]
        for number, item in enumerate(note.items, 1):
            if kept := [cls.observation_line(observation) for observation in item.observations]:
                lines += ["", f"### {number}. {item.topic or item.request}", "", *kept]
        if links := [(item, link) for item in note.items for link in item.links]:
            lines += ["", "## 링크 후보", ""]
            lines += [f'- {link.relation} {link.project} — {link.description} (근거 #{item.number}: "{link.quote}")' for item, link in links]
        return "\n".join(lines).rstrip() + "\n"

    @staticmethod
    def render_session(job: Job, note: SessionNote, summarizer: str, target: NoteTarget) -> str:
        post = frontmatter.Post(content="")
        started, ended = target.window
        post.metadata = {
            "title": NoteService.dated(note.header.title, started, ended, target.zone),
            "type": "note",
            "tags": [f"project/{target.project}", f"agent/{job.agent}", f"device/{job.device}", *NoteService.clean_tags(note.header.tags)],
            "source": {
                "agent": job.agent,
                "model": job.model,
                "device": job.device,
                "session_id": job.session_id,
                "cwd": job.cwd,
                "project_inference": job.project_inference.value,
                "summarizer": summarizer,
                "started_at": started.isoformat() if started else None,
                "ended_at": ended.isoformat() if ended else None,
                "ingested_at": datetime.now(UTC).isoformat(),
            },
        }
        post.content = NoteService.body_of_session(note)
        return frontmatter.dumps(post)

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
    def render_overview(project: str, result: SummaryResult, summarizer: str, *, logs: int) -> str:
        post = frontmatter.Post(content="")
        post.metadata = {
            "title": result.title,
            "type": "note",
            "tags": [f"project/{project}", "kind/overview", *NoteService.clean_tags(result.tags)],
            "source": {
                "kind": "overview",
                "project": project,
                "logs": logs,
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

    def path_for(self, job: Job, project: str, started: datetime | None, zone: tzinfo) -> str:
        day = (started or job.started_at or job.ended_at or job.created_at).astimezone(zone).strftime("%Y-%m-%d")
        return f"{PROJECTS_ROOT}/{project}/{LOG_DIR}/{day}-{slugify(job.agent)}-{job.id}.md"

    def write(self, job: Job, result: SummaryResult, summarizer: str, project: str, zone: tzinfo = UTC) -> str:
        default = self.path_for(job, project, None, zone)
        return self.repository.write(job.note_path or default, self.render(job, result, summarizer).rstrip() + "\n")

    def write_session(self, job: Job, note: SessionNote, summarizer: str, target: NoteTarget) -> str:
        default = self.path_for(job, target.project, target.window[0], target.zone)
        rendered = self.render_session(job, note, summarizer, target).rstrip() + "\n"
        kept = job.note_path if job.note_path and job.note_path.startswith(f"{PROJECTS_ROOT}/{target.project}/") else None
        return self.repository.write(default if target.sidecar else kept or default, rendered)

    def forget_session(self, agent: str, device: str, session_id: str) -> None:
        held = [
            summary.path
            for summary in self.repository.list_summaries()
            if tuple(str(summary.source.get(key) or "") for key in ("agent", "device", "session_id")) == (agent, device, session_id)
        ]
        for path in held:
            self.repository.delete(path)

    def forget_others(self, job: Job, kept: set[str]) -> int:
        stale = [
            path
            for path in self.repository.root.glob(f"{PROJECTS_ROOT}/*/{LOG_DIR}/*{job.id}*{self.repository.suffix}")
            if path.relative_to(self.repository.root).as_posix() not in kept
        ]
        for path in stale:
            path.unlink()
        return len(stale)

    def relocate(self, relative_path: str, wanted: str, project: str) -> str | None:
        current = self.repository.resolve(relative_path)
        if current is None:
            return None
        content = current.read_text(encoding="utf-8")
        written = self.repository.write(wanted, content)
        self.repository.delete(relative_path)
        self.refile(written, project)
        return written

    def refile(self, relative_path: str, project: str) -> None:
        target = self.repository.resolve(relative_path)
        if target is None:
            return
        try:
            post = frontmatter.load(target)
        except Exception:
            logger.warning("leaving %s alone: its frontmatter is unreadable", relative_path, exc_info=True)
            return

        post.metadata.pop("permalink", None)
        tags = post.metadata.get("tags")
        if isinstance(tags, list):
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
    def overview_path(project: str) -> str:
        return f"{PROJECTS_ROOT}/{project}/{OVERVIEW_FILE}"

    def overview(self, project: str) -> NoteDetail | None:
        path = self.overview_path(project)
        return self.repository.retrieve(path) if self.repository.resolve(path) is not None else None

    def forget_overview(self, project: str) -> bool:
        return self.repository.delete(self.overview_path(project))

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
    def memory_dir(project: str) -> str:
        return f"{PROJECTS_ROOT}/{project}/{MEMORY_DIR}"

    def list_memories(self, project: str) -> list[MemoryFile]:
        texts = self.repository.read_directory(self.memory_dir(project))
        return [MemoryFile(name=name, content=self.restore_memory(text)) for name, text in texts.items()]

    def memory_descriptions(self, project: str) -> list[tuple[str, str]]:
        texts = self.repository.read_directory(self.memory_dir(project))
        return [(name, self.description_of(text)) for name, text in texts.items() if name != "MEMORY.md"]

    def sync_memories(self, project: str, payload: MemorySync) -> MemorySyncResult:
        base = self.memory_dir(project)
        written = [self.repository.write(f"{base}/{file.name}", self.render_memory(payload, file)) for file in payload.files]
        deleted = [path for path in (f"{base}/{name}" for name in payload.deleted) if self.repository.delete(path)]
        return MemorySyncResult(written=written, deleted=deleted)

    def recent_logs(self, project: str, limit: int) -> list[NoteDetail]:
        logs = [summary for summary in self.repository.list_summaries(project) if f"/{LOG_DIR}/" in summary.path]
        return [self.repository.retrieve(summary.path) for summary in logs[:limit]]

    def list_notes(self, project: str | None = None) -> list[NoteSummary]:
        return self.repository.list_summaries(project)

    def retrieve(self, relative_path: str) -> NoteDetail:
        return self.repository.retrieve(relative_path)

    def search(self, query: str, limit: int = SEARCH_RESULT_LIMIT) -> list[NoteSummary]:
        return self.repository.grep(query.lower(), limit)


noteServiceDI = Annotated[NoteService, Depends(NoteService)]  # noqa: N816
