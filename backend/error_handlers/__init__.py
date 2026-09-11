from __future__ import annotations

from typing import TYPE_CHECKING, Any

from backend.error_handlers import err_argon2, err_default, err_pydantic, err_pyjwt

if TYPE_CHECKING:
    from collections.abc import Callable, Coroutine

    from starlette.requests import Request
    from starlette.responses import Response

type ErrHandlerType = Callable[[Request, Any], Coroutine[Any, Any, Response]]


def get_error_handlers() -> dict[int | type[Exception], ErrHandlerType]:
    return {
        **err_default.error_handler_patterns,
        **err_pydantic.error_handler_patterns,
        **err_pyjwt.error_handler_patterns,
        **err_argon2.error_handler_patterns,
    }
