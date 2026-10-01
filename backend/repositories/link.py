from __future__ import annotations

from typing import TYPE_CHECKING, Annotated

from fastapi import Depends
from sqlalchemy import or_
from sqlmodel import col, select

from backend.models import LinkKind, ProjectLink
from backend.repositories import DBRepositoryImpl

if TYPE_CHECKING:
    from collections.abc import Sequence


class ProjectLinkRepository(DBRepositoryImpl[ProjectLink]):
    model = ProjectLink
    resource = "project link"

    async def find(self, source: str, target: str) -> ProjectLink | None:
        query = select(ProjectLink).where(
            or_(
                (col(ProjectLink.source) == source) & (col(ProjectLink.target) == target),
                (col(ProjectLink.source) == target) & (col(ProjectLink.target) == source),
            ),
        )
        return (await self.session.exec(query)).first()

    async def around(self, project: str) -> Sequence[ProjectLink]:
        query = select(ProjectLink).where(or_(col(ProjectLink.source) == project, col(ProjectLink.target) == project))
        return (await self.session.exec(query)).all()

    async def all_links(self) -> Sequence[ProjectLink]:
        return (await self.session.exec(select(ProjectLink).order_by(col(ProjectLink.source)))).all()

    async def would_cycle(self, source: str, target: str) -> bool:
        replaced = frozenset((source, target))
        upward: dict[str, set[str]] = {}
        for link in await self.all_links():
            if link.kind is LinkKind.PART_OF and not link.unrelated and frozenset((link.source, link.target)) != replaced:
                upward.setdefault(link.source, set()).add(link.target)
        seen: set[str] = set()
        frontier = [target]
        while frontier:
            current = frontier.pop()
            if current == source:
                return True
            if current not in seen:
                seen.add(current)
                frontier.extend(upward.get(current, ()))
        return False

    async def settled(self, kind: LinkKind, source: str, target: str) -> LinkKind:
        if kind is LinkKind.PART_OF and await self.would_cycle(source, target):
            return LinkKind.RELATED
        return kind

    async def forget(self, slugs: list[str]) -> int:
        found = [link for link in await self.all_links() if link.source in slugs or link.target in slugs]
        for link in found:
            await self.session.delete(link)
        await self.session.commit()
        return len(found)


projectLinkRepositoryDI = Annotated[ProjectLinkRepository, Depends(ProjectLinkRepository)]  # noqa: N816
