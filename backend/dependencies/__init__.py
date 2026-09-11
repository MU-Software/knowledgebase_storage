from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Annotated

from fastapi import Depends
from sqlmodel.ext.asyncio.session import AsyncSession

from backend.settings import ProjectSetting, get_settings

if TYPE_CHECKING:
    from collections.abc import AsyncIterator


async def get_session() -> AsyncIterator[AsyncSession]:
    async with get_settings().async_session_maker() as session:
        yield session


def get_notes_dir() -> Path:
    return get_settings().notes_dir


dbDI = Annotated[AsyncSession, Depends(get_session)]  # noqa: N816
settingsDI = Annotated[ProjectSetting, Depends(get_settings)]  # noqa: N816
notesDirDI = Annotated[Path, Depends(get_notes_dir)]  # noqa: N816
