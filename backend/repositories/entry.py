from __future__ import annotations

from typing import TYPE_CHECKING, Annotated

from fastapi import Depends
from sqlmodel import col, select

from backend.models import ProjectEntry
from backend.repositories import DBRepositoryImpl

if TYPE_CHECKING:
    from collections.abc import Sequence


class ProjectEntryRepository(DBRepositoryImpl[ProjectEntry]):
    model = ProjectEntry
    resource = "project entry"

    async def find(self, slug: str) -> ProjectEntry | None:
        return (await self.session.exec(select(ProjectEntry).where(col(ProjectEntry.slug) == slug))).first()

    async def all_entries(self) -> Sequence[ProjectEntry]:
        return (await self.session.exec(select(ProjectEntry).order_by(col(ProjectEntry.slug)))).all()

    async def sharing(self, path: str) -> Sequence[ProjectEntry]:
        found = await self.all_entries()
        return [entry for entry in found if any(ProjectEntry.covers(str(source.get("path") or ""), path) for source in entry.sources)]

    async def forget(self, slugs: list[str]) -> int:
        found = [entry for entry in await self.all_entries() if entry.slug in slugs]
        for entry in found:
            await self.session.delete(entry)
        await self.session.commit()
        return len(found)


projectEntryRepositoryDI = Annotated[ProjectEntryRepository, Depends(ProjectEntryRepository)]  # noqa: N816
