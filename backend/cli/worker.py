from __future__ import annotations

import asyncio
import logging
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from socket import gethostname
from typing import TYPE_CHECKING, Any

import typer
from httpx import AsyncClient, codes

from backend.schemas import WorkerConfig
from backend.summarizers import build_summarizer

if TYPE_CHECKING:
    from backend.schemas import LLMProviderResolved

logger = logging.getLogger(__name__)

API_TIMEOUT_SECONDS = 60.0
FALLBACK_POLL_SECONDS = 15.0
MAX_ERROR_LENGTH = 2000


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


def partition(config: WorkerConfig, job: dict[str, Any]) -> tuple[list[LLMProviderResolved], int | None]:
    age = (datetime.now(UTC) - datetime.fromisoformat(job["last_activity_at"])).total_seconds()
    ready = [provider for provider in config.providers if age >= provider.min_job_age_seconds]
    waits = [int(provider.min_job_age_seconds - age) for provider in config.providers if age < provider.min_job_age_seconds]
    return ready, min(waits) if waits else None


async def release(api: AsyncClient, job_id: str, token: str, retry_after: int) -> None:
    with suppress(Exception):
        await api.post(f"/api/jobs/{job_id}/release", params={"token": token, "retry_after": retry_after})


async def process(api: AsyncClient, job: dict[str, Any], config: WorkerConfig) -> bool:
    job_id, token = job["id"], job["claim_token"]
    errors: list[str] = []

    providers, next_eligible_in = partition(config, job)
    if not providers:
        logger.info("no provider is eligible for job %s yet; releasing it", job_id)
        await release(api, job_id, token, next_eligible_in or 0)
        return False

    for provider in providers:
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


async def run(api_url: str, api_key: str, name: str) -> None:
    cache = ConfigCache()
    async with AsyncClient(base_url=api_url, headers={"X-API-Key": api_key}, timeout=API_TIMEOUT_SECONDS) as api:
        logger.info("worker %s started, polling %s", name, api_url)
        while True:
            config: WorkerConfig | None = None
            idle = True
            try:
                config = await cache.load(api)
                claimed = await api.post("/api/jobs/claim", params={"worker": name})
                claimed.raise_for_status()
                idle = config is None or claimed.status_code == codes.NO_CONTENT
            except Exception:
                logger.exception("failed to claim a job")

            if idle or config is None:
                await asyncio.sleep(config.poll_interval_seconds if config else FALLBACK_POLL_SECONDS)
                continue

            if not await process(api, claimed.json(), config):
                await asyncio.sleep(config.poll_interval_seconds)


def worker(
    api_url: str = typer.Option(..., envvar="KBSTORE_API_URL", help="Base URL of the knowledgebase API."),
    api_key: str = typer.Option(..., envvar="KBSTORE_API_KEY", help="The api's WORKER_API_KEY."),
    name: str | None = typer.Option(None, help="Worker name recorded on claimed jobs. Defaults to the hostname."),
    log_level: str = typer.Option("INFO", help="Logging level."),
) -> None:
    logging.basicConfig(level=log_level)
    asyncio.run(run(api_url, api_key, name or gethostname()))
