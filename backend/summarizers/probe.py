from __future__ import annotations

import logging
from time import perf_counter
from typing import TYPE_CHECKING, Any

from httpx import HTTPStatusError

from backend.schemas import ProviderTestResult
from backend.summarizers import build_summarizer

if TYPE_CHECKING:
    from backend.schemas import LLMProviderResolved

logger = logging.getLogger(__name__)

PROBE_TIMEOUT_SECONDS = 60.0
MAX_DETAIL_LENGTH = 1000

PROBE_TRANSCRIPT: list[dict[str, Any]] = [
    {"role": "user", "content": "Add a health check endpoint to the API."},
    {"role": "assistant", "content": "Added GET /healthz returning 200. We decided to leave the readiness probe for later."},
]


def elapsed_ms(started: float) -> int:
    return int((perf_counter() - started) * 1000)


def describe(exc: Exception) -> str:
    # llama.cpp puts the reason in the body, which raise_for_status() leaves out
    body = f": {exc.response.text}" if isinstance(exc, HTTPStatusError) else ""
    return f"{type(exc).__name__}: {exc}{body}"[:MAX_DETAIL_LENGTH]


async def probe(provider: LLMProviderResolved, language: str) -> ProviderTestResult:
    """Summarize a throwaway transcript, so a pass means the provider answers the way the worker needs it to."""
    capped = provider.model_copy(update={"timeout_seconds": min(provider.timeout_seconds, PROBE_TIMEOUT_SECONDS)})
    started = perf_counter()
    try:
        result = await build_summarizer(capped, language).summarize(PROBE_TRANSCRIPT, agent="probe", project="_probe", device="settings")
    except Exception as exc:  # noqa: BLE001
        logger.info("provider %s failed its probe: %s", provider.name, exc)
        return ProviderTestResult(ok=False, latency_ms=elapsed_ms(started), detail=describe(exc))
    return ProviderTestResult(ok=True, latency_ms=elapsed_ms(started), detail=result.title)
