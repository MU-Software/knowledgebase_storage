from __future__ import annotations

from typing import TYPE_CHECKING, Annotated

from fastapi import Depends
from sqlalchemy import delete, update
from sqlmodel import col, select

from backend.models import ProjectAlias
from backend.repositories import DBRepositoryImpl

if TYPE_CHECKING:
    from collections.abc import Sequence


class ProjectAliasRepository(DBRepositoryImpl[ProjectAlias]):
    model = ProjectAlias
    resource = "project alias"

    async def target(self, source: str) -> str | None:
        alias = (await self.session.exec(select(ProjectAlias).where(col(ProjectAlias.source) == source))).first()
        return alias.slug if alias is not None else None

    async def all(self) -> Sequence[ProjectAlias]:
        return await self.list(order_by=[col(ProjectAlias.source)])

    async def redirect(self, source_slug: str, target_slug: str) -> None:
        await self.session.exec(delete(ProjectAlias).where(col(ProjectAlias.source).in_([source_slug, target_slug])))
        await self.session.exec(update(ProjectAlias).where(col(ProjectAlias.slug) == source_slug).values(slug=target_slug))
        self.session.add(ProjectAlias(source=source_slug, slug=target_slug))
        await self.session.commit()

    async def forget(self, slug: str) -> None:
        await self.session.exec(delete(ProjectAlias).where(col(ProjectAlias.slug) == slug))
        await self.session.commit()


projectAliasRepositoryDI = Annotated[ProjectAliasRepository, Depends(ProjectAliasRepository)]  # noqa: N816
