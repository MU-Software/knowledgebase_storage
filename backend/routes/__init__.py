from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import APIRouter, Depends

from backend.dependencies.auth import authenticate
from backend.routes.auth import router as auth_router
from backend.routes.frontend import register_frontend
from backend.routes.health import router as health_router
from backend.routes.jobs import router as jobs_router
from backend.routes.settings import router as settings_router
from backend.routes.wiki import router as wiki_router

if TYPE_CHECKING:
    from fastapi import FastAPI


def register_routes(app: FastAPI) -> None:
    protected_router = APIRouter(dependencies=[Depends(authenticate)])
    protected_router.include_router(jobs_router)
    protected_router.include_router(settings_router)
    protected_router.include_router(wiki_router)

    api_router = APIRouter(prefix="/api")
    api_router.include_router(auth_router)
    api_router.include_router(protected_router)

    app.include_router(health_router)
    app.include_router(api_router)
    register_frontend(app)
