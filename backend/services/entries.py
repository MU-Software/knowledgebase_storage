from __future__ import annotations

from typing import TYPE_CHECKING, Annotated

from fastapi import Depends

from backend.models import ProjectEntry
from backend.repositories.entry import ProjectEntryRepository, projectEntryRepositoryDI
from backend.schemas import ProjectEntryPublic, ProjectEntryWrite
from backend.services import ServiceImpl
from backend.services.projects import projectServiceDI

if TYPE_CHECKING:
    from collections.abc import Sequence

DESCRIPTION_CHARS = 200


class EntryService(ServiceImpl[ProjectEntryRepository]):
    repository: projectEntryRepositoryDI
    projects: projectServiceDI

    async def write(self, payload: ProjectEntryWrite) -> ProjectEntryPublic:
        slug = await self.projects.resolve(payload.slug)
        entry = await self.repository.find(slug) or ProjectEntry(slug=slug)
        entry.description = payload.description
        entry.sources = [source.model_dump() for source in payload.sources]
        entry.container = payload.container
        return ProjectEntryPublic.model_validate(await self.repository.save(entry), from_attributes=True)

    async def listed(self) -> Sequence[ProjectEntry]:
        return await self.repository.all_entries()

    @staticmethod
    def named(described: list[str]) -> list[str]:
        return [line.removeprefix("- ").partition(" (")[0].strip() for line in described]

    async def candidates(self, path: str) -> tuple[dict[str, str], str, list[str]]:
        sharing = await self.repository.sharing(path) if path else []
        prefixes = {entry.slug: entry.prefix_for(path) for entry in sharing if not entry.container}
        container = next((entry.slug for entry in sharing if entry.container), "")
        described = [f"- {entry.slug} ({entry.prefix_for(path) or 'repository root'}): {entry.description[:DESCRIPTION_CHARS]}" for entry in sharing]
        return {slug: prefix for slug, prefix in prefixes.items() if prefix}, container, described

    async def describe(self, slugs: list[str]) -> list[str]:
        known = {entry.slug: entry for entry in await self.repository.all_entries()}
        lines = []
        for slug in slugs:
            entry = known.get(slug)
            lines.append(f"- {slug}: {entry.description[:DESCRIPTION_CHARS]}" if entry and entry.description else f"- {slug}")
        return lines


entryServiceDI = Annotated[EntryService, Depends(EntryService)]  # noqa: N816
