from __future__ import annotations

from typing import TYPE_CHECKING

from backend.errors import ServerError

if TYPE_CHECKING:
    from starlette.requests import Request
    from starlette.responses import JSONResponse

    from backend.error_handlers import ErrHandlerType


async def exception_handler(_req: Request, _err: Exception) -> JSONResponse:
    return ServerError.UNKNOWN_SERVER_ERROR.response()


error_handler_patterns: dict[int | type[Exception], ErrHandlerType] = {Exception: exception_handler}
