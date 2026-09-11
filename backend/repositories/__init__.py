from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar, Generic, TypedDict, TypeVar, Unpack

from pydantic import BaseModel, ConfigDict
from sqlalchemy import true, update
from sqlmodel import SQLModel, select

from backend.dependencies import dbDI, notesDirDI
from backend.errors import ClientError

if TYPE_CHECKING:
    from collections.abc import Iterator, Sequence
    from pathlib import Path
    from uuid import UUID

    from sqlalchemy.orm import Mapped
    from sqlalchemy.sql.elements import ColumnElement, UnaryExpression

M = TypeVar("M", bound=SQLModel)

type QueryType = ColumnElement[bool]
type OrderByType = list[ColumnElement | UnaryExpression | Mapped]


class ListKwargsType(TypedDict, total=False):
    query_filter: QueryType
    order_by: OrderByType
    offset: int
    limit: int
    with_for_update: bool
    skip_locked: bool


class RepositoryImpl(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    resource: ClassVar[str] = "resource"


class DBRepositoryImpl(RepositoryImpl, Generic[M]):
    session: dbDI

    model: ClassVar[type[M]]

    @property
    def order_by(self) -> OrderByType:
        return []

    async def retrieve_by_id(self, obj_id: UUID, *, with_for_update: bool = False) -> M:
        instance = await self.session.get(self.model, obj_id, with_for_update=with_for_update or None)
        if instance is None:
            ClientError.RESOURCE_NOT_FOUND.format_msg(resource=self.resource).raise_()
        return instance

    async def list(self, **kwargs: Unpack[ListKwargsType]) -> Sequence[M]:
        query = (
            select(self.model)
            .where(kwargs.get("query_filter", true()))
            .order_by(*kwargs.get("order_by", self.order_by))
            .offset(kwargs.get("offset"))
            .limit(kwargs.get("limit"))
        )
        if kwargs.get("with_for_update"):
            query = query.with_for_update(skip_locked=kwargs.get("skip_locked", False))
        return (await self.session.exec(query)).all()

    async def bulk_update(self, query_filter: QueryType, **values: object) -> int:
        result = await self.session.exec(update(self.model).where(query_filter).values(**values))
        await self.session.commit()
        return int(result.rowcount or 0)

    async def save(self, instance: M) -> M:
        self.session.add(instance)
        await self.session.commit()
        await self.session.refresh(instance)
        return instance


class FSRepositoryImpl(RepositoryImpl):
    root: notesDirDI

    suffix: ClassVar[str] = ".md"

    def contain(self, relative_path: str) -> Path | None:
        target = (self.root / relative_path).resolve()
        return target if target.is_relative_to(self.root) else None

    def iter_files(self, subpath: str | None = None) -> Iterator[Path]:
        base = self.root if subpath is None else self.contain(subpath)
        if base is None or not base.is_dir():
            return
        yield from sorted(base.rglob(f"*{self.suffix}"))

    def resolve(self, relative_path: str) -> Path | None:
        target = self.contain(relative_path)
        return target if target is not None and target.is_file() else None

    def write(self, relative_path: str, content: str) -> str:
        target = self.root / relative_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        return target.relative_to(self.root).as_posix()
