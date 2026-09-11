from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, TypeVar

import typer
from fastapi.exceptions import RequestValidationError

from backend.errors import BackendException
from backend.services.user import UserService
from backend.settings import get_settings

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

T = TypeVar("T")


def run(work: Callable[[UserService], Awaitable[T]]) -> T:
    async def main() -> T:
        settings = get_settings()
        try:
            async with settings.async_session_maker() as session:
                return await work(UserService.for_session(session))
        finally:
            await settings.async_engine.dispose()

    try:
        result = asyncio.run(main())
    except BackendException as exc:
        raise typer.BadParameter(exc.error.msg) from exc
    except RequestValidationError as exc:
        raise typer.BadParameter(" ".join(error.msg for error in exc.errors())) from exc
    return result


def create_user(
    username: str = typer.Argument(..., help="Name to sign in with."),
    password: str = typer.Option(..., prompt=True, hide_input=True, confirmation_prompt=True, envvar="KBSTORE_PASSWORD"),
) -> None:
    run(lambda service: service.create_user(username, password))
    typer.echo(f"created user {username}")


def set_password(
    username: str = typer.Argument(..., help="User whose password changes."),
    password: str = typer.Option(..., prompt="New password", hide_input=True, confirmation_prompt=True, envvar="KBSTORE_PASSWORD"),
) -> None:
    run(lambda service: service.set_password(username, password))
    typer.echo(f"changed the password of {username}")
