from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import APIRouter

from backend.routes.frontend import register_frontend
from backend.routes.health import router as health_router
from backend.routes.jobs import router as jobs_router
from backend.routes.settings import router as settings_router
from backend.routes.wiki import router as wiki_router

if TYPE_CHECKING:
    from fastapi import FastAPI


def register_routes(app: FastAPI) -> None:
    api_router = APIRouter(prefix="/api")
    api_router.include_router(jobs_router)
    api_router.include_router(settings_router)
    api_router.include_router(wiki_router)

    app.include_router(health_router)
    app.include_router(api_router)
    register_frontend(app)
