from __future__ import annotations

from hashlib import sha256
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Header, Response, status
from sqlmodel import col

from backend.models import LLMProvider
from backend.repositories.setting import llmProviderRepositoryDI, runtimeSettingRepositoryDI
from backend.schemas import (
    LLMProviderCreate,
    LLMProviderPublic,
    LLMProviderResolved,
    LLMProviderUpdate,
    ProviderTestResult,
    RuntimeSettingPublic,
    RuntimeSettingUpdate,
    WorkerConfig,
)
from backend.summarizers.probe import probe

router = APIRouter(tags=["settings"])


def _public(provider: LLMProvider) -> LLMProviderPublic:
    return LLMProviderPublic(**provider.model_dump(exclude={"api_key"}), has_api_key=bool(provider.api_key))


def _resolved(provider: LLMProvider) -> LLMProviderResolved:
    return LLMProviderResolved(**provider.model_dump(), has_api_key=bool(provider.api_key))


@router.get("/settings")
async def read_settings(repository: runtimeSettingRepositoryDI) -> RuntimeSettingPublic:
    return RuntimeSettingPublic.model_validate(await repository.get(), from_attributes=True)


@router.patch("/settings")
async def update_settings(payload: RuntimeSettingUpdate, repository: runtimeSettingRepositoryDI) -> RuntimeSettingPublic:
    setting = await repository.get()
    setting.sqlmodel_update(payload.model_dump(exclude_unset=True, exclude_none=True))
    return RuntimeSettingPublic.model_validate(await repository.save(setting), from_attributes=True)


@router.get("/llm-providers")
async def list_providers(repository: llmProviderRepositoryDI) -> list[LLMProviderPublic]:
    return [_public(provider) for provider in await repository.list(order_by=[col(LLMProvider.priority)])]


@router.post("/llm-providers", status_code=status.HTTP_201_CREATED)
async def create_provider(payload: LLMProviderCreate, repository: llmProviderRepositoryDI) -> LLMProviderPublic:
    return _public(await repository.save(LLMProvider(**payload.model_dump())))


@router.patch("/llm-providers/{provider_id}")
async def update_provider(provider_id: UUID, payload: LLMProviderUpdate, repository: llmProviderRepositoryDI) -> LLMProviderPublic:
    provider = await repository.retrieve_by_id(provider_id)
    provider.sqlmodel_update(payload.model_dump(exclude_unset=True, exclude_none=True))
    return _public(await repository.save(provider))


@router.post("/llm-providers/{provider_id}/test")
async def test_provider(
    provider_id: UUID,
    repository: llmProviderRepositoryDI,
    settings: runtimeSettingRepositoryDI,
) -> ProviderTestResult:
    """Summarize a throwaway transcript with this provider, whether or not it is enabled."""
    provider = await repository.retrieve_by_id(provider_id)
    return await probe(_resolved(provider), (await settings.get()).document_language)


@router.delete("/llm-providers/{provider_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_provider(provider_id: UUID, repository: llmProviderRepositoryDI) -> None:
    await repository.delete(await repository.retrieve_by_id(provider_id))


@router.get("/worker-config", response_model=WorkerConfig | None)
async def worker_config(
    response: Response,
    providers: llmProviderRepositoryDI,
    settings: runtimeSettingRepositoryDI,
    if_none_match: Annotated[str | None, Header()] = None,
) -> WorkerConfig | Response:
    runtime = await settings.get()
    config = WorkerConfig(
        document_language=runtime.document_language,
        poll_interval_seconds=runtime.worker_poll_interval_seconds,
        providers=[_resolved(provider) for provider in await providers.list_active()],
    )
    etag = f'W/"{sha256(config.model_dump_json().encode()).hexdigest()[:32]}"'
    if if_none_match == etag:
        return Response(status_code=status.HTTP_304_NOT_MODIFIED)

    response.headers["ETag"] = etag
    return config
