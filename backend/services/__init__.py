from __future__ import annotations

from typing import Generic, TypeVar

from pydantic import BaseModel, ConfigDict

from backend.repositories import RepositoryImpl

R = TypeVar("R", bound=RepositoryImpl)


class ServiceImpl(BaseModel, Generic[R]):
    repository: R

    model_config = ConfigDict(arbitrary_types_allowed=True)
