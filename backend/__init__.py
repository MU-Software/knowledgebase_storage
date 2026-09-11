from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager, suppress
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from fastapi import FastAPI

from backend.error_handlers import get_error_handlers
from backend.repositories.auth import LoginFailureRepository
from backend.repositories.job import JobRepository
from backend.repositories.setting import RuntimeSettingRepository
from backend.routes import register_routes
from backend.settings import get_settings

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

logger = logging.getLogger(__name__)

DEFAULT_JANITOR_INTERVAL_SECONDS = 60


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    settings.notes_dir.mkdir(parents=True, exist_ok=True)

    async def janitor() -> None:
        interval = DEFAULT_JANITOR_INTERVAL_SECONDS
        while True:
            try:
                async with settings.async_session_maker() as session:
                    runtime = await RuntimeSettingRepository(session=session).get()
                    repository = JobRepository(session=session)
                    if reclaimed := await repository.reclaim_stale(timedelta(hours=runtime.stale_claim_hours)):
                        logger.info("returned %d job(s) abandoned mid-run to the queue", reclaimed)
                    if purged := await repository.purge_transcripts(timedelta(hours=runtime.transcript_retention_hours)):
                        logger.info("discarded %d transcript(s) past their retention window", purged)
                    window_start = datetime.now(UTC) - timedelta(minutes=runtime.login_failure_window_minutes)
                    if forgotten := await LoginFailureRepository(session=session).purge(window_start):
                        logger.info("forgot %d sign-in failure(s) past their window", forgotten)
                    interval = runtime.maintenance_interval_seconds
            except Exception:
                logger.exception("janitorial pass failed")
            await asyncio.sleep(interval)

    task = asyncio.create_task(janitor())
    try:
        yield
    finally:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task


def create_app() -> FastAPI:
    settings = get_settings()
    logging.basicConfig(level=settings.log_level)
    app = FastAPI(
        title="knowledgebase-storage",
        description="Collects, summarizes and publishes project knowledge from many devices and LLM services.",
        debug=settings.debug,
        lifespan=lifespan,
        exception_handlers=get_error_handlers(),
        docs_url="/docs" if settings.debug else None,
        redoc_url="/redoc" if settings.debug else None,
        openapi_url="/openapi.json" if settings.debug else None,
    )
    register_routes(app)
    return app
