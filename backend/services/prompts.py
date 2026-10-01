from __future__ import annotations

from typing import TYPE_CHECKING, Annotated

from fastapi import Depends

from backend.consts.prompts import DEFAULTS
from backend.models import Prompt, PromptStage, PromptStatus
from backend.repositories.prompt import PromptRepository, promptRepositoryDI
from backend.repositories.setting import runtimeSettingRepositoryDI
from backend.schemas import PromptPublic, PromptUpdate, PromptWrite
from backend.services import ServiceImpl

BUILT_IN = "built-in"
EDITED = "edited"

if TYPE_CHECKING:
    from collections.abc import Sequence
    from uuid import UUID


class PromptService(ServiceImpl[PromptRepository]):
    repository: promptRepositoryDI
    runtime: runtimeSettingRepositoryDI

    async def seed(self) -> int:
        known = await self.repository.all_active()
        made = 0
        for stage, defaults in DEFAULTS.items():
            current = known.get(stage)
            if current is None:
                await self.repository.save(Prompt(stage=stage, label=BUILT_IN, status=PromptStatus.ACTIVE, **defaults))
                made += 1
            elif current.label == BUILT_IN and current.parent_id is None and any(getattr(current, key) != value for key, value in defaults.items()):
                current.sqlmodel_update(defaults)
                await self.repository.save(current)
                made += 1
        return made

    async def resolved(self) -> dict[str, dict[str, object]]:
        await self.seed()
        language = (await self.runtime.get()).document_language
        return {
            stage.value: {
                "id": str(prompt.id),
                "system": prompt.system.replace("{language}", language),
                "instruction": prompt.instruction.replace("{language}", language),
                "thinking": prompt.thinking,
                "temperature": prompt.temperature,
                "max_tokens": prompt.max_tokens,
            }
            for stage, prompt in (await self.repository.all_active()).items()
        }

    async def list_prompts(self, stage: PromptStage | None) -> Sequence[Prompt]:
        await self.seed()
        return await self.repository.history(stage)

    async def draft(self, payload: PromptWrite) -> Prompt:
        await self.seed()
        parent = await self.repository.active(payload.stage)
        label = EDITED if payload.label == BUILT_IN else payload.label
        kept = {**payload.model_dump(), "label": label}
        return await self.repository.save(Prompt(**kept, status=PromptStatus.DRAFT, parent_id=parent.id if parent else None))

    async def amend(self, prompt_id: UUID, payload: PromptUpdate) -> Prompt:
        prompt = await self.repository.retrieve_by_id(prompt_id)
        if prompt.status is not PromptStatus.DRAFT:
            return await self.draft(PromptWrite.model_validate(prompt.model_copy(update=payload.model_dump(exclude_none=True)).model_dump()))
        prompt.sqlmodel_update(payload.model_dump(exclude_none=True))
        prompt.label = EDITED if prompt.label == BUILT_IN else prompt.label
        return await self.repository.save(prompt)

    async def activate(self, prompt_id: UUID) -> PromptPublic:
        prompt = await self.repository.retrieve_by_id(prompt_id)
        prompt.status = PromptStatus.ACTIVE
        saved = await self.repository.save(prompt)
        await self.repository.retire(saved.stage, saved.id)
        return PromptPublic.model_validate(saved, from_attributes=True)


promptServiceDI = Annotated[PromptService, Depends(PromptService)]  # noqa: N816
