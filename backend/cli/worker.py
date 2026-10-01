from __future__ import annotations

import asyncio
import logging
from contextlib import contextmanager, suppress
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import PurePosixPath
from socket import gethostname
from typing import TYPE_CHECKING, Any

import typer
from httpx import AsyncClient, codes

from backend.models import JobKind, ProviderKind
from backend.schemas import SessionItem, SessionNote, WorkerConfig
from backend.summarizers import build_summarizer
from backend.summarizers.base import Toolbox
from backend.summarizers.session import MAX_VERIFIED, SessionPipeline

if TYPE_CHECKING:
    from collections.abc import Generator

    from backend.schemas import LLMProviderResolved

logger = logging.getLogger(__name__)

API_TIMEOUT_SECONDS = 60.0
FALLBACK_POLL_SECONDS = 15.0
MAX_ERROR_LENGTH = 2000

SlotKey = tuple[str, int]


@dataclass
class ConfigCache:
    config: WorkerConfig | None = None
    etag: str | None = None

    async def load(self, api: AsyncClient) -> WorkerConfig | None:
        headers = {"If-None-Match": self.etag} if self.etag else {}
        response = await api.get("/api/worker-config", headers=headers)
        if response.status_code == codes.NOT_MODIFIED:
            return self.config

        response.raise_for_status()
        self.config = WorkerConfig.model_validate(response.json())
        self.etag = response.headers.get("ETag")
        logger.info("reloaded worker config: %d provider(s)", len(self.config.providers))
        return self.config

    def provider(self, name: str) -> LLMProviderResolved | None:
        return next((provider for provider in self.providers if provider.name == name), None)

    @property
    def providers(self) -> list[LLMProviderResolved]:
        return self.config.providers if self.config else []

    @property
    def poll_interval(self) -> float:
        return self.config.poll_interval_seconds if self.config else FALLBACK_POLL_SECONDS


@dataclass
class Gate:
    inflight: dict[str, int] = field(default_factory=dict)

    @contextmanager
    def seat(self, provider: LLMProviderResolved) -> Generator[bool]:
        taken = self.inflight.get(provider.name, 0)
        seated = taken < provider.max_concurrency
        if seated:
            self.inflight[provider.name] = taken + 1
        try:
            yield seated
        finally:
            if seated:
                self.inflight[provider.name] -= 1


def partition(providers: list[LLMProviderResolved], job: dict[str, Any]) -> tuple[list[LLMProviderResolved], int | None]:
    age = (datetime.now(UTC) - datetime.fromisoformat(job["last_activity_at"])).total_seconds()
    ready = [provider for provider in providers if age >= provider.min_job_age_seconds]
    waits = [int(provider.min_job_age_seconds - age) for provider in providers if age < provider.min_job_age_seconds]
    return ready, min(waits) if waits else None


def fallback_order(config: WorkerConfig, first: LLMProviderResolved, job: dict[str, Any]) -> list[LLMProviderResolved]:
    order = [first, *(provider for provider in config.providers if provider.name != first.name)]
    if job.get("priority"):
        order = [provider for provider in order if provider.background_jobs]
    if not job.get("refined_at"):
        return order

    previous = next((provider for provider in config.providers if provider.name == job.get("summarizer")), None)
    return [first] if previous is None else [provider for provider in order if provider.priority < previous.priority]


GIT_SPECS = [
    {
        "type": "function",
        "function": {
            "name": "search_changes",
            "description": "Commits whose diff adds or removes lines matching an extended regex, with the files they touched.",
            "parameters": {
                "type": "object",
                "properties": {"pattern": {"type": "string"}, "since": {"type": "string"}, "until": {"type": "string"}},
                "required": ["pattern"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "git_log",
            "description": "Commits in a time range with the files each one changed.",
            "parameters": {
                "type": "object",
                "properties": {"since": {"type": "string"}, "until": {"type": "string"}, "path": {"type": "string"}},
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "git_show",
            "description": "One commit's message and diff, optionally limited to a path.",
            "parameters": {"type": "object", "properties": {"commit": {"type": "string"}, "path": {"type": "string"}}, "required": ["commit"]},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Numbered lines of a file. Leave rev out for the newest commit.",
            "parameters": {
                "type": "object",
                "properties": {"rev": {"type": "string"}, "path": {"type": "string"}, "start": {"type": "integer"}, "end": {"type": "integer"}},
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "grep",
            "description": "Search a revision's files with an extended regex.",
            "parameters": {
                "type": "object",
                "properties": {"rev": {"type": "string"}, "pattern": {"type": "string"}, "path": {"type": "string"}},
                "required": ["rev", "pattern"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "worktree_diff",
            "description": "Uncommitted changes the session left behind, optionally one path. Use this when nothing was committed.",
            "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": []},
        },
    },
]
COMMANDS = {
    "search_changes": "log",
    "git_log": "log",
    "git_show": "show",
    "read_file": "read_file",
    "grep": "grep",
    "worktree_diff": "worktree",
}


def git_toolbox(api: AsyncClient, network_id: str) -> Toolbox:
    async def run(name: str, arguments: dict[str, Any]) -> str:
        body = {
            "network_id": network_id,
            "command": COMMANDS.get(name, "log"),
            "rev": arguments.get("rev") or arguments.get("commit") or "",
            "path": arguments.get("path") or "",
            "pattern": arguments.get("pattern") or "",
            "since": arguments.get("since") or "",
            "until": arguments.get("until") or "",
            "start": int(arguments.get("start") or 1),
            "end": int(arguments.get("end") or 200),
        }
        try:
            response = await api.post("/api/git/query", json=body)
            response.raise_for_status()
        except Exception as exc:  # noqa: BLE001
            return f"tool failed: {exc}"
        return str(response.json().get("output") or "")

    return Toolbox(specs=GIT_SPECS, run=run, rounds=6)


def moment(value: object) -> str:
    try:
        return datetime.fromisoformat(str(value)).astimezone().strftime("%Y-%m-%d %H:%M:%S %z")
    except ValueError:
        return ""


def background_of(job: dict[str, Any], plan: dict[str, Any]) -> str:
    lines = [f"This session's project: {job['project']}"]
    if window := " to ".join(filter(None, (moment(plan.get("started_at")), moment(plan.get("ended_at"))))):
        lines.append(f"It ran from {window}. Copy a timestamp with its offset when a tool asks for one.")
    parts = ["\n".join(lines)]
    if state := plan.get("repository"):
        parts.append(f"The repository as it stands now:\n{state}")
    if projects := plan.get("projects"):
        parts.append("Other known projects:\n" + "\n".join(projects))
    if context := job.get("context"):
        parts.append(f"Notes from the projects this one sits under:\n{context}")
    return "\n\n".join(parts) + "\n\n---\n\n"


async def produce_session(api: AsyncClient, job: dict[str, Any], provider: LLMProviderResolved, language: str) -> tuple[str, dict[str, Any]] | None:
    plan = job.get("plan") or {}
    segments = plan.get("segments") or []
    prompts = job.get("prompts") or {}
    if not segments or "session_chunk" not in prompts:
        return None
    summarizer = build_summarizer(provider, language)
    network_id = plan.get("network_id")
    tools = git_toolbox(api, str(network_id)) if network_id and provider.kind == ProviderKind.LLAMA else None
    verified = plan.get("verify_max_claims")
    pipeline = SessionPipeline(summarizer, prompts, tools, MAX_VERIFIED if verified is None else int(verified))
    note = await pipeline.run(segments, background_of(job, plan))
    candidates = plan.get("candidates") or []
    filed = await pipeline.filed(segments, plan.get("split") or [], candidates, plan.get("names") or [], job["project"])
    return "note", {"notes": [note.model_dump(mode="json") for note in shared(note, filed, job["project"])]}


def shared(note: SessionNote, filed: dict[int, str], container: str) -> list[SessionNote]:
    if not filed:
        return [note]
    owners = {number: project for number, project in filed.items() if project}
    grouped: dict[str, list[SessionItem]] = {}
    for item in note.items:
        grouped.setdefault(owners.get(item.number, container), []).append(item)
    if len(grouped) == 1:
        return [note.model_copy(update={"project": next(iter(grouped))})]
    return [note.model_copy(update={"items": items, "project": project}) for project, items in grouped.items()]


async def produce(job: dict[str, Any], provider: LLMProviderResolved, language: str) -> tuple[str, dict[str, Any]]:
    summarizer = build_summarizer(provider, language)
    kind, project = job.get("kind"), job["project"]
    records, targets = job.get("transcript") or [], job.get("targets") or []

    if kind == JobKind.MEMORY_MERGE:
        name = PurePosixPath(job.get("note_path") or "memory.md").name
        return "memory", {"content": await summarizer.merge_memories(records, project=project, name=name)}
    if kind == JobKind.PROJECT_OVERVIEW:
        page = await summarizer.summarize_project(records, project=project)
        return "overview", page.model_dump(mode="json")
    if kind == JobKind.PROJECT_LINK:
        stage = (job.get("prompts") or {}).get("project_link") or {}
        verdict = await summarizer.judge_pair(records, str(stage.get("system", "")), stage)
        return "link", verdict.model_dump(mode="json")
    if kind == JobKind.NOTE_RELATIONS:
        return "relations", (await summarizer.find_relations(records, candidates=targets)).model_dump(mode="json")

    result = await summarizer.summarize(
        records,
        agent=job["agent"],
        project=project,
        device=job["device"],
        background=job.get("context", ""),
    )
    return "result", result.model_dump(mode="json")


async def release(api: AsyncClient, job_id: str, token: str, retry_after: int) -> None:
    with suppress(Exception):
        await api.post(f"/api/jobs/{job_id}/release", params={"token": token, "retry_after": retry_after})


async def process(api: AsyncClient, job: dict[str, Any], config: WorkerConfig, first: LLMProviderResolved, gate: Gate) -> bool:
    job_id, token = job["id"], job["claim_token"]
    errors: list[str] = []
    busy = False

    providers, next_eligible_in = partition(fallback_order(config, first, job), job)
    if not providers:
        logger.info("no provider is eligible for job %s yet; releasing it", job_id)
        await release(api, job_id, token, next_eligible_in or 0)
        return False

    for provider in providers:
        with gate.seat(provider) as seated:
            if not seated:
                logger.info("provider %s is already at capacity; leaving job %s to it", provider.name, job_id)
                busy = True
                continue

            logger.info("working job %s (%s) with %s (project=%s)", job_id, job.get("kind"), provider.name, job["project"])
            try:
                shaped = await produce_session(api, job, provider, config.document_language) if job.get("kind") == JobKind.SESSION else None
                endpoint, body = shaped if shaped is not None else await produce(job, provider, config.document_language)
            except Exception as exc:  # noqa: BLE001
                logger.warning("provider %s failed on job %s: %s", provider.name, job_id, exc)
                errors.append(f"{provider.name}: {exc}")
                continue

        try:
            response = await api.post(
                f"/api/jobs/{job_id}/{endpoint}",
                params={"summarizer": provider.name, "token": token},
                json=body,
            )
            if response.status_code != codes.CONFLICT:
                response.raise_for_status()
        except Exception:
            logger.exception("failed to submit the result of job %s", job_id)
            return False
        if response.status_code == codes.CONFLICT:
            logger.info("job %s was settled while it ran, so its result is dropped", job_id)
        else:
            logger.info("job %s done via %s", job_id, provider.name)
        return True

    if busy:
        logger.info("every provider is busy or down for job %s (%s); handing it back", job_id, "; ".join(errors) or "none failed")
        await release(api, job_id, token, next_eligible_in or 0)
        return False

    if next_eligible_in is not None:
        logger.info("every ready provider failed on job %s; waiting %ds for the next one", job_id, next_eligible_in)
        await release(api, job_id, token, next_eligible_in)
        return False

    with suppress(Exception):
        await api.post(
            f"/api/jobs/{job_id}/fail",
            params={"token": token},
            json={"error": "; ".join(errors)[:MAX_ERROR_LENGTH]},
        )
    return False


async def slot(api: AsyncClient, worker: str, key: SlotKey, cache: ConfigCache, gate: Gate) -> None:
    """One provider claims for itself, so every provider works on its own job at the same time."""
    provider_name, seat = key
    while True:
        config, provider = cache.config, cache.provider(provider_name)
        if config is None or provider is None or seat >= provider.max_concurrency:
            return

        job: dict[str, Any] | None = None
        try:
            claimed = await api.post(
                "/api/jobs/claim",
                params={
                    "worker": f"{worker}:{provider.name}",
                    "provider": provider.name,
                    "min_age_seconds": provider.min_job_age_seconds,
                },
            )
            claimed.raise_for_status()
            job = None if claimed.status_code == codes.NO_CONTENT else claimed.json()
        except Exception:
            logger.exception("%s failed to claim a job", provider.name)

        if job is None or not await process(api, job, config, provider, gate):
            await asyncio.sleep(config.poll_interval_seconds)


async def supervise(api: AsyncClient, worker: str, cache: ConfigCache) -> None:
    """Keep one slot per provider seat alive, following whatever the wiki currently says."""
    slots: dict[SlotKey, asyncio.Task[None]] = {}
    gate = Gate()
    try:
        while True:
            try:
                await cache.load(api)
            except Exception:
                logger.exception("failed to reload the worker config")

            for key in [key for key, task in slots.items() if task.done()]:
                if (exc := slots.pop(key).exception()) is not None:
                    logger.error("slot %s stopped and will be restarted: %s", key, exc)

            wanted = {(provider.name, seat) for provider in cache.providers for seat in range(provider.max_concurrency)}
            for key in wanted - slots.keys():
                slots[key] = asyncio.create_task(slot(api, worker, key, cache, gate), name=f"{key[0]}#{key[1]}")

            await asyncio.sleep(cache.poll_interval)
    finally:
        for task in slots.values():
            task.cancel()
        await asyncio.gather(*slots.values(), return_exceptions=True)


async def run(api_url: str, api_key: str, name: str) -> None:
    async with AsyncClient(base_url=api_url, headers={"X-API-Key": api_key}, timeout=API_TIMEOUT_SECONDS) as api:
        logger.info("worker %s started, polling %s", name, api_url)
        await supervise(api, name, ConfigCache())


def worker(
    api_url: str = typer.Option(..., envvar="KBSTORE_API_URL", help="Base URL of the knowledgebase API."),
    api_key: str = typer.Option(..., envvar="KBSTORE_API_KEY", help="The api's WORKER_API_KEY."),
    name: str | None = typer.Option(None, help="Worker name recorded on claimed jobs. Defaults to the hostname."),
    log_level: str = typer.Option("INFO", help="Logging level."),
) -> None:
    logging.basicConfig(level=log_level)
    asyncio.run(run(api_url, api_key, name or gethostname()))
