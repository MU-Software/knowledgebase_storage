from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query, status

from backend.models import PromptStage
from backend.schemas import PromptPublic, PromptUpdate, PromptWrite
from backend.services.prompts import promptServiceDI

router = APIRouter(prefix="/prompts", tags=["prompts"])


@router.get("")
async def list_prompts(service: promptServiceDI, stage: Annotated[PromptStage | None, Query()] = None) -> list[PromptPublic]:
    found = await service.list_prompts(stage)
    return [PromptPublic.model_validate(prompt, from_attributes=True) for prompt in found]


@router.post("", status_code=status.HTTP_201_CREATED)
async def draft_prompt(payload: PromptWrite, service: promptServiceDI) -> PromptPublic:
    return PromptPublic.model_validate(await service.draft(payload), from_attributes=True)


@router.patch("/{prompt_id}")
async def amend_prompt(prompt_id: UUID, payload: PromptUpdate, service: promptServiceDI) -> PromptPublic:
    return PromptPublic.model_validate(await service.amend(prompt_id, payload), from_attributes=True)


@router.post("/{prompt_id}/activate")
async def activate_prompt(prompt_id: UUID, service: promptServiceDI) -> PromptPublic:
    return await service.activate(prompt_id)
