from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Annotated

from fastapi import Depends
from sqlalchemy import delete, or_
from sqlmodel import col, select

from backend.models import ProjectSuggestion
from backend.repositories import DBRepositoryImpl

if TYPE_CHECKING:
    from collections.abc import Sequence
    from uuid import UUID

    from backend.models import SuggestionKind


class ProjectSuggestionRepository(DBRepositoryImpl[ProjectSuggestion]):
    model = ProjectSuggestion
    resource = "suggestion"

    async def list_open(self) -> Sequence[ProjectSuggestion]:
        query = (
            select(ProjectSuggestion)
            .where(col(ProjectSuggestion.applied_at).is_(None), col(ProjectSuggestion.dismissed_at).is_(None))
            .order_by(col(ProjectSuggestion.created_at))
        )
        return (await self.session.exec(query)).all()

    async def seen(self, kind: SuggestionKind, source: str, target: str) -> bool:
        query = select(ProjectSuggestion).where(
            col(ProjectSuggestion.kind) == kind,
            col(ProjectSuggestion.source) == source,
            col(ProjectSuggestion.target) == target,
        )
        return (await self.session.exec(query)).first() is not None

    async def decide(self, suggestion_id: UUID, *, applied: bool) -> None:
        stamp = datetime.now(UTC)
        field = "applied_at" if applied else "dismissed_at"
        await self.bulk_update(col(ProjectSuggestion.id) == suggestion_id, **{field: stamp})

    async def forget(self, slugs: list[str]) -> int:
        result = await self.session.exec(
            delete(ProjectSuggestion).where(
                or_(col(ProjectSuggestion.source).in_(slugs), col(ProjectSuggestion.target).in_(slugs)),
            ),
        )
        await self.session.commit()
        return int(result.rowcount or 0)


projectSuggestionRepositoryDI = Annotated[ProjectSuggestionRepository, Depends(ProjectSuggestionRepository)]  # noqa: N816
