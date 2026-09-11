from __future__ import annotations

from typing import TYPE_CHECKING

from jwt.exceptions import PyJWTError

from backend.errors import AuthNError

if TYPE_CHECKING:
    from starlette.requests import Request
    from starlette.responses import JSONResponse

    from backend.error_handlers import ErrHandlerType


async def jwt_error_handler(_req: Request, _err: PyJWTError) -> JSONResponse:
    return AuthNError.INVALID_TOKEN.response()


error_handler_patterns: dict[int | type[Exception], ErrHandlerType] = {PyJWTError: jwt_error_handler}
