from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import status
from pydantic_core import ValidationError
from starlette.responses import JSONResponse

if TYPE_CHECKING:
    from starlette.requests import Request

    from backend.error_handlers import ErrHandlerType


async def pydantic_validationerror_handler(_req: Request, err: ValidationError) -> JSONResponse:
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        content={"detail": err.errors(include_context=False, include_input=False)},
    )


error_handler_patterns: dict[int | type[Exception], ErrHandlerType] = {ValidationError: pydantic_validationerror_handler}
