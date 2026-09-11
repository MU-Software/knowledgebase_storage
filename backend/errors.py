from __future__ import annotations

from enum import StrEnum
from logging import getLogger
from re import sub
from typing import Any, ClassVar, NoReturn, TypedDict, Unpack, cast

from fastapi import status
from fastapi.exceptions import HTTPException, RequestValidationError
from pydantic import BaseModel, Field
from starlette.responses import JSONResponse

from backend.consts.cookies import CookieKey

logger = getLogger(__name__)


def camel_to_snake_case(camel: str) -> str:
    return sub("([a-z0-9])([A-Z])", r"\1_\2", sub("(.)([A-Z][a-z]+)", r"\1_\2", camel)).lower()


class ErrorStructDict(TypedDict, total=False):
    type: str
    msg: str
    loc: list[str]
    input: Any
    ctx: dict[str, Any]

    status_code: int
    should_log: bool


class BackendException(HTTPException):
    error: ErrorStruct
    detail: list[ErrorStructDict]  # type: ignore[assignment]

    def __init__(self, error: ErrorStruct) -> None:
        self.error = error
        super().__init__(
            status_code=error.status_code,
            detail=[cast("ErrorStructDict", error.model_dump(exclude_none=True, exclude_defaults=True))],
        )


class ErrorStruct(BaseModel):
    type: str
    msg: str
    loc: list[str] | None = None
    input: Any | None = None
    ctx: dict[str, Any] | None = None

    status_code: int = Field(default=status.HTTP_500_INTERNAL_SERVER_ERROR, exclude=True)
    should_log: bool = Field(default=True, exclude=True)

    def __call__(self, **kwargs: Unpack[ErrorStructDict]) -> ErrorStruct:
        return self.model_copy(update=kwargs)

    def format_msg(self, *args: object, **kwargs: object) -> ErrorStruct:
        return self(msg=self.msg.format(*args, **kwargs))

    def raise_(self) -> NoReturn:
        if self.should_log:
            logger.error("%s:%s:%s", self.type, self.status_code, self.msg)
        if self.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT:
            raise RequestValidationError(errors=[self])
        raise BackendException(self)

    def response(self) -> JSONResponse:
        return JSONResponse(status_code=self.status_code, content={"detail": [self.model_dump(exclude_none=True, exclude_defaults=True)]})


class ErrorEnumMixin:
    __default_args__: ClassVar[ErrorStructDict] = {}
    __additional_args__: ClassVar[dict[str, ErrorStructDict]] = {}


class ErrorEnum(ErrorEnumMixin, StrEnum):
    def __call__(self, **kwargs: Unpack[ErrorStructDict]) -> ErrorStruct:
        return ErrorStruct(
            **cast(
                "ErrorStructDict",
                {
                    "type": camel_to_snake_case(f"{self.__class__.__name__}.{self.name}"),
                    "msg": self.value,
                    **self.__default_args__,
                    **self.__additional_args__.get(self.name, {}),
                    **kwargs,
                },
            ),
        )

    def raise_(self, **kwargs: Unpack[ErrorStructDict]) -> NoReturn:
        self(**kwargs).raise_()

    def response(self, **kwargs: Unpack[ErrorStructDict]) -> JSONResponse:
        return self(**kwargs).response()

    def format_msg(self, *args: object, **kwargs: object) -> ErrorStruct:
        return self().format_msg(*args, **kwargs)


class ServerError(ErrorEnum):
    __default_args__: ClassVar[ErrorStructDict] = {"status_code": status.HTTP_500_INTERNAL_SERVER_ERROR, "should_log": True}

    UNKNOWN_SERVER_ERROR = "Something went wrong on the server. Try again in a few minutes."


class AuthNError(ErrorEnum):
    __default_args__: ClassVar[ErrorStructDict] = {"status_code": status.HTTP_401_UNAUTHORIZED, "should_log": False}
    __additional_args__: ClassVar[dict[str, ErrorStructDict]] = {
        "INVALID_REFRESH_TOKEN": ErrorStructDict(loc=["cookie", CookieKey.REFRESH_TOKEN.get_name()]),
        "INVALID_API_KEY": ErrorStructDict(loc=["header", "x-api-key"]),
        "SIGNIN_FAILED": ErrorStructDict(loc=["body", "username"]),
        "SIGNIN_THROTTLED": ErrorStructDict(status_code=status.HTTP_429_TOO_MANY_REQUESTS),
        "AUTHN_FAILED_AS_HEADER_NOT_PROVIDED": ErrorStructDict(loc=["cookie", CookieKey.CSRF_TOKEN.get_name()]),
    }

    INVALID_TOKEN = "Your credentials are invalid or expired. Sign in again."  # noqa: S105
    INVALID_REFRESH_TOKEN = "Your sign-in has expired. Sign in again."  # noqa: S105
    INVALID_API_KEY = "The API key is invalid, expired or deleted."
    SIGNIN_REQUIRED = "Sign in, or send an API key in the X-API-Key header."
    SIGNIN_FAILED = "Invalid username or password."
    SIGNIN_THROTTLED = "Too many failed sign-in attempts. Try again in a few minutes."
    AUTHN_FAILED_AS_HEADER_NOT_PROVIDED = "The CSRF cookie is missing. Reload the page and try again."


class AuthZError(ErrorEnum):
    __default_args__: ClassVar[ErrorStructDict] = {"status_code": status.HTTP_403_FORBIDDEN, "should_log": False}

    PERMISSION_DENIED = "This needs a signed-in browser; an API key cannot do it."


class ClientError(ErrorEnum):
    __default_args__: ClassVar[ErrorStructDict] = {"status_code": status.HTTP_422_UNPROCESSABLE_CONTENT, "should_log": False}
    __additional_args__: ClassVar[dict[str, ErrorStructDict]] = {
        "RESOURCE_NOT_FOUND": ErrorStructDict(status_code=status.HTTP_404_NOT_FOUND),
        "RESOURCE_ALREADY_EXISTS": ErrorStructDict(status_code=status.HTTP_409_CONFLICT),
        "STALE_CLAIM": ErrorStructDict(status_code=status.HTTP_409_CONFLICT),
        "INVALID_NOTE_PATH": ErrorStructDict(status_code=status.HTTP_400_BAD_REQUEST),
    }

    RESOURCE_NOT_FOUND = "The {resource} was not found."
    RESOURCE_ALREADY_EXISTS = "The {resource} already exists."
    STALE_CLAIM = "Job {job_id} is no longer held by this claim."
    INVALID_NOTE_PATH = "A note path must stay inside the notes directory and end with .md: {path}"

    USERNAME_REQUIRED = "Enter a username."
    USERNAME_TOO_SHORT = "The username is too short. Use {min_len} to {max_len} characters."
    USERNAME_TOO_LONG = "The username is too long. Use {min_len} to {max_len} characters."
    USERNAME_CONTAINS_INVALID_CHAR = "The username may only contain letters, digits, - and _."

    PASSWORD_REQUIRED = "Enter a password."  # noqa: S105
    PASSWORD_TOO_SHORT = "The password is too short. Use {min_len} to {max_len} characters."  # noqa: S105
    PASSWORD_TOO_LONG = "The password is too long. Use at most {max_len} characters."  # noqa: S105
    PASSWORD_CONTAINS_INVALID_CHAR = "The password contains characters that are not allowed."  # noqa: S105
    PASSWORD_NEED_MORE_CHAR_TYPE = "The password needs at least {min_char_type_num} of lowercase, uppercase, digits and punctuation."  # noqa: S105
