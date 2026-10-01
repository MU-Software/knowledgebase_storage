from __future__ import annotations

from typing import TYPE_CHECKING, Annotated

from fastapi import Depends
from sqlmodel import col, select

from backend.models import Prompt, PromptStage, PromptStatus
from backend.repositories import DBRepositoryImpl

if TYPE_CHECKING:
    from collections.abc import Sequence
    from uuid import UUID


class PromptRepository(DBRepositoryImpl[Prompt]):
    model = Prompt
    resource = "prompt"

    async def active(self, stage: PromptStage) -> Prompt | None:
        query = select(Prompt).where(col(Prompt.stage) == stage, col(Prompt.status) == PromptStatus.ACTIVE)
        return (await self.session.exec(query)).first()

    async def all_active(self) -> dict[PromptStage, Prompt]:
        query = select(Prompt).where(col(Prompt.status) == PromptStatus.ACTIVE)
        return {prompt.stage: prompt for prompt in (await self.session.exec(query)).all()}

    async def history(self, stage: PromptStage | None) -> Sequence[Prompt]:
        query = select(Prompt).order_by(col(Prompt.stage), col(Prompt.created_at).desc())
        if stage is not None:
            query = query.where(col(Prompt.stage) == stage)
        return (await self.session.exec(query)).all()

    async def retire(self, stage: PromptStage, keep: UUID) -> int:
        return await self.bulk_update(
            (col(Prompt.stage) == stage) & (col(Prompt.status) == PromptStatus.ACTIVE) & (col(Prompt.id) != keep),
            status=PromptStatus.ARCHIVED,
        )


promptRepositoryDI = Annotated[PromptRepository, Depends(PromptRepository)]  # noqa: N816
