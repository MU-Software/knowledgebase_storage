from __future__ import annotations

from typing import TYPE_CHECKING, Annotated

from fastapi import Depends
from sqlmodel import col, select

from backend.models import NoteDecision, NotePin, NoteVerdict
from backend.repositories import DBRepositoryImpl

if TYPE_CHECKING:
    from collections.abc import Sequence


class NoteDecisionRepository(DBRepositoryImpl[NoteDecision]):
    model = NoteDecision
    resource = "note decision"

    async def of(self, agent: str, device: str, session_id: str) -> NoteDecision | None:
        query = select(NoteDecision).where(
            col(NoteDecision.agent) == agent,
            col(NoteDecision.device) == device,
            col(NoteDecision.session_id) == session_id,
        )
        return (await self.session.exec(query)).first()

    async def held_back(self, agent: str, device: str, session_id: str) -> bool:
        return await self.of(agent, device, session_id) is not None

    async def purged(self, agent: str, device: str, session_id: str) -> bool:
        decision = await self.of(agent, device, session_id)
        return decision is not None and decision.verdict is NoteVerdict.PURGE

    async def all_decisions(self) -> Sequence[NoteDecision]:
        return (await self.session.exec(select(NoteDecision).order_by(col(NoteDecision.created_at).desc()))).all()


noteDecisionRepositoryDI = Annotated[NoteDecisionRepository, Depends(NoteDecisionRepository)]  # noqa: N816


class NotePinRepository(DBRepositoryImpl[NotePin]):
    model = NotePin
    resource = "note pin"

    async def find(self, agent: str, device: str, session_id: str, first_request: int) -> NotePin | None:
        query = select(NotePin).where(
            col(NotePin.agent) == agent,
            col(NotePin.device) == device,
            col(NotePin.session_id) == session_id,
            col(NotePin.first_request) == first_request,
        )
        return (await self.session.exec(query)).first()

    async def around(self, agent: str, device: str, session_id: str) -> Sequence[NotePin]:
        query = select(NotePin).where(
            col(NotePin.agent) == agent,
            col(NotePin.device) == device,
            col(NotePin.session_id) == session_id,
        )
        return (await self.session.exec(query)).all()

    async def carry(self, source: str, target: str) -> int:
        found = (await self.session.exec(select(NotePin).where(col(NotePin.project) == source))).all()
        for pin in found:
            pin.project = target
            self.session.add(pin)
        await self.session.flush()
        return len(found)

    async def all_pins(self) -> Sequence[NotePin]:
        return (await self.session.exec(select(NotePin).order_by(col(NotePin.created_at).desc()))).all()


notePinRepositoryDI = Annotated[NotePinRepository, Depends(NotePinRepository)]  # noqa: N816
