from __future__ import annotations

from typing import TYPE_CHECKING, Annotated

from fastapi import Depends
from sqlmodel import col, select

from backend.models import RawTranscript, Upload
from backend.repositories import DBRepositoryImpl

if TYPE_CHECKING:
    from collections.abc import Sequence


class RawTranscriptRepository(DBRepositoryImpl[RawTranscript]):
    model = RawTranscript
    resource = "raw transcript"

    async def find_by_session(self, agent: str, device: str, session_id: str) -> RawTranscript | None:
        query = select(RawTranscript).where(
            col(RawTranscript.agent) == agent,
            col(RawTranscript.device) == device,
            col(RawTranscript.session_id) == session_id,
        )
        return (await self.session.exec(query)).first()

    async def list_by_device(self, device: str, limit: int) -> Sequence[RawTranscript]:
        query = select(RawTranscript).where(col(RawTranscript.device) == device).order_by(col(RawTranscript.ended_at).desc()).limit(limit)
        return (await self.session.exec(query)).all()

    async def rebuildable(self, device: str | None, project: str | None, limit: int, offset: int) -> Sequence[RawTranscript]:
        query = select(RawTranscript).order_by(col(RawTranscript.created_at), col(RawTranscript.id)).offset(offset).limit(limit)
        if device:
            query = query.where(col(RawTranscript.device) == device)
        if project:
            query = query.where(col(RawTranscript.project_hint) == project)
        return (await self.session.exec(query)).all()


class UploadRepository(DBRepositoryImpl[Upload]):
    model = Upload
    resource = "upload"


rawTranscriptRepositoryDI = Annotated[RawTranscriptRepository, Depends(RawTranscriptRepository)]  # noqa: N816
uploadRepositoryDI = Annotated[UploadRepository, Depends(UploadRepository)]  # noqa: N816
