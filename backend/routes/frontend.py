from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import Response, status
from fastapi.responses import FileResponse, JSONResponse

from backend.errors import InvalidNotePathError, ResourceNotFoundError, StaleClaimError
from backend.settings import FRONTEND_PATH

if TYPE_CHECKING:
    from fastapi import FastAPI, Request

API_PREFIX = "/api"


def register_frontend(app: FastAPI) -> None:
    @app.exception_handler(ResourceNotFoundError)
    async def domain_not_found(request: Request, exc: ResourceNotFoundError) -> Response:  # noqa: ARG001
        return JSONResponse({"detail": str(exc)}, status_code=status.HTTP_404_NOT_FOUND)

    @app.exception_handler(InvalidNotePathError)
    async def invalid_note_path(request: Request, exc: InvalidNotePathError) -> Response:  # noqa: ARG001
        return JSONResponse({"detail": str(exc)}, status_code=status.HTTP_400_BAD_REQUEST)

    @app.exception_handler(StaleClaimError)
    async def stale_claim(request: Request, exc: StaleClaimError) -> Response:  # noqa: ARG001
        return JSONResponse({"detail": str(exc)}, status_code=status.HTTP_409_CONFLICT)

    @app.exception_handler(status.HTTP_404_NOT_FOUND)
    async def spa_fallback(request: Request, exc: Exception) -> Response:
        if request.url.path.startswith(API_PREFIX) or not FRONTEND_PATH.exists():
            return JSONResponse({"detail": getattr(exc, "detail", "not found")}, status_code=status.HTTP_404_NOT_FOUND)
        return FileResponse(FRONTEND_PATH, media_type="text/html")

    @app.get("/favicon.ico", include_in_schema=False)
    async def favicon() -> Response:
        return Response(status_code=status.HTTP_204_NO_CONTENT)
