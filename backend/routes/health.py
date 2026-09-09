from __future__ import annotations

from fastapi import APIRouter, Response, status
from sqlalchemy import text

from backend.dependencies import dbDI, notesDirDI

router = APIRouter(tags=["meta"], include_in_schema=False)


@router.get("/livez")
async def livez() -> dict[str, str]:
    return {"status": "alive"}


@router.get("/readyz")
async def readyz(session: dbDI, notes_dir: notesDirDI, response: Response) -> dict[str, object]:
    checks: dict[str, str] = {}

    try:
        await session.exec(text("SELECT 1"))  # type: ignore[call-overload]
    except Exception as exc:  # noqa: BLE001
        checks["database"] = f"unavailable: {exc}"
    else:
        checks["database"] = "ok"

    checks["notes_dir"] = "ok" if notes_dir.is_dir() else f"missing: {notes_dir}"

    if failed := [name for name, result in checks.items() if result != "ok"]:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return {"status": "not ready", "failed": failed, "checks": checks}
    return {"status": "ready", "checks": checks}
