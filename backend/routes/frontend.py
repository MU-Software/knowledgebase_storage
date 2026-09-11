from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import Response, status
from fastapi.responses import FileResponse, JSONResponse

from backend.settings import FRONTEND_PATH

if TYPE_CHECKING:
    from fastapi import FastAPI, Request

API_PREFIX = "/api"


def register_frontend(app: FastAPI) -> None:
    @app.exception_handler(status.HTTP_404_NOT_FOUND)
    async def spa_fallback(request: Request, exc: Exception) -> Response:
        if request.url.path.startswith(API_PREFIX) or not FRONTEND_PATH.exists():
            return JSONResponse({"detail": getattr(exc, "detail", "not found")}, status_code=status.HTTP_404_NOT_FOUND)
        return FileResponse(FRONTEND_PATH, media_type="text/html")

    @app.get("/favicon.ico", include_in_schema=False)
    async def favicon() -> Response:
        return Response(status_code=status.HTTP_204_NO_CONTENT)
