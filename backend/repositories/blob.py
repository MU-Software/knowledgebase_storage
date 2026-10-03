from __future__ import annotations

from typing import TYPE_CHECKING, Annotated
from uuid import uuid4

from fastapi import Depends
from sqlalchemy.dialects.postgresql import insert
from sqlmodel import col, select

from backend.models import Blob, GitSource, WorktreeFile
from backend.repositories import DBRepositoryImpl

if TYPE_CHECKING:
    from collections.abc import Sequence
    from uuid import UUID


class BlobRepository(DBRepositoryImpl[Blob]):
    model = Blob
    resource = "blob"

    async def find(self, digest: str, *, with_for_update: bool = False) -> Blob | None:
        query = select(Blob).where(col(Blob.digest) == digest)
        if with_for_update:
            query = query.with_for_update().execution_options(populate_existing=True)
        return (await self.session.exec(query)).first()

    async def claim(self, digest: str) -> Blob:
        await self.session.exec(
            insert(Blob).values(id=uuid4(), digest=digest, byte_size=0, chunks=[]).on_conflict_do_nothing(index_elements=["digest"])
        )
        query = select(Blob).where(col(Blob.digest) == digest).with_for_update().execution_options(populate_existing=True)
        return (await self.session.exec(query)).one()

    async def completed(self, digests: list[str]) -> set[str]:
        query = select(Blob.digest).where(col(Blob.digest).in_(digests), col(Blob.completed_at).is_not(None))
        return set((await self.session.exec(query)).all())


class WorktreeFileRepository(DBRepositoryImpl[WorktreeFile]):
    model = WorktreeFile
    resource = "worktree file"

    async def list_by_source(self, source_id: UUID) -> Sequence[WorktreeFile]:
        return (await self.session.exec(select(WorktreeFile).where(col(WorktreeFile.source_id) == source_id))).all()

    async def list_in_network(self, network_id: UUID) -> Sequence[tuple[WorktreeFile, str]]:
        query = (
            select(WorktreeFile, GitSource.device)
            .join(GitSource, col(GitSource.id) == col(WorktreeFile.source_id))
            .where(col(GitSource.network_id) == network_id)
            .order_by(col(WorktreeFile.path), col(WorktreeFile.modified_at).desc())
        )
        return (await self.session.exec(query)).all()

    async def save_all(self, files: list[WorktreeFile]) -> None:
        self.session.add_all(files)
        await self.session.commit()

    async def referenced(self, digests: list[str]) -> set[str]:
        return set((await self.session.exec(select(WorktreeFile.digest).where(col(WorktreeFile.digest).in_(digests)))).all())


blobRepositoryDI = Annotated[BlobRepository, Depends(BlobRepository)]  # noqa: N816
worktreeFileRepositoryDI = Annotated[WorktreeFileRepository, Depends(WorktreeFileRepository)]  # noqa: N816
