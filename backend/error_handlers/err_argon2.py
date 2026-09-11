from __future__ import annotations

from typing import TYPE_CHECKING

from argon2.exceptions import InvalidHashError, VerificationError

from backend.errors import AuthNError

if TYPE_CHECKING:
    from starlette.requests import Request
    from starlette.responses import JSONResponse

    from backend.error_handlers import ErrHandlerType


async def argon2_error_handler(_req: Request, _err: VerificationError | InvalidHashError) -> JSONResponse:
    return AuthNError.SIGNIN_FAILED.response()


error_handler_patterns: dict[int | type[Exception], ErrHandlerType] = {
    VerificationError: argon2_error_handler,
    InvalidHashError: argon2_error_handler,
}
