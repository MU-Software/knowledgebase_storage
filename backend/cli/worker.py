from __future__ import annotations

import asyncio
import logging
from contextlib import contextmanager, suppress
from dataclasses import dataclass, field
from datetime import UTC, datetime
from socket import gethostname
from typing import TYPE_CHECKING, Any

import typer
from httpx import AsyncClient, codes

from backend.schemas import WorkerConfig
from backend.summarizers import build_summarizer

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


def fallback_order(config: WorkerConfig, first: LLMProviderResolved) -> list[LLMProviderResolved]:
    return [first, *(provider for provider in config.providers if provider.name != first.name)]


async def release(api: AsyncClient, job_id: str, token: str, retry_after: int) -> None:
    with suppress(Exception):
        await api.post(f"/api/jobs/{job_id}/release", params={"token": token, "retry_after": retry_after})


async def process(api: AsyncClient, job: dict[str, Any], config: WorkerConfig, first: LLMProviderResolved, gate: Gate) -> bool:
    job_id, token = job["id"], job["claim_token"]
    errors: list[str] = []
    busy = False

    providers, next_eligible_in = partition(fallback_order(config, first), job)
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

            logger.info("summarizing job %s with %s (project=%s)", job_id, provider.name, job["project"])
            try:
                summarizer = build_summarizer(provider, config.document_language)
                result = await summarizer.summarize(job["transcript"], agent=job["agent"], project=job["project"], device=job["device"])
            except Exception as exc:  # noqa: BLE001
                logger.warning("provider %s failed on job %s: %s", provider.name, job_id, exc)
                errors.append(f"{provider.name}: {exc}")
                continue

        try:
            response = await api.post(
                f"/api/jobs/{job_id}/result",
                params={"summarizer": provider.name, "token": token},
                json=result.model_dump(mode="json"),
            )
            response.raise_for_status()
        except Exception:
            logger.exception("failed to submit the result of job %s", job_id)
            return False
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
                params={"worker": f"{worker}:{provider.name}", "min_age_seconds": provider.min_job_age_seconds},
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
