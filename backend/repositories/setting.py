from __future__ import annotations

from typing import TYPE_CHECKING, Annotated

from fastapi import Depends
from sqlmodel import col, select

from backend.models import SINGLETON_ID, LLMProvider, RuntimeSetting
from backend.repositories import DBRepositoryImpl

if TYPE_CHECKING:
    from collections.abc import Sequence


class RuntimeSettingRepository(DBRepositoryImpl[RuntimeSetting]):
    model = RuntimeSetting
    resource = "runtime setting"

    async def get(self) -> RuntimeSetting:
        if (setting := await self.session.get(RuntimeSetting, SINGLETON_ID)) is None:
            setting = await self.save(RuntimeSetting(id=SINGLETON_ID))
        return setting


class LLMProviderRepository(DBRepositoryImpl[LLMProvider]):
    model = LLMProvider
    resource = "llm provider"

    async def list_active(self) -> Sequence[LLMProvider]:
        query = select(LLMProvider).where(col(LLMProvider.enabled).is_(True)).order_by(col(LLMProvider.priority))
        return (await self.session.exec(query)).all()

    async def delete(self, provider: LLMProvider) -> None:
        await self.session.delete(provider)
        await self.session.commit()


runtimeSettingRepositoryDI = Annotated[RuntimeSettingRepository, Depends(RuntimeSettingRepository)]  # noqa: N816
llmProviderRepositoryDI = Annotated[LLMProviderRepository, Depends(LLMProviderRepository)]  # noqa: N816
