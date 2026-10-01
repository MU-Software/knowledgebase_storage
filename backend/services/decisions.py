from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Annotated

from fastapi import Depends

from backend.consts.notes import LOG_DIR, PROJECTS_ROOT
from backend.models import JobStatus, NoteDecision, NotePin, NoteVerdict
from backend.repositories.decision import NoteDecisionRepository, noteDecisionRepositoryDI, notePinRepositoryDI
from backend.repositories.job import jobRepositoryDI
from backend.repositories.raw import rawTranscriptRepositoryDI
from backend.repositories.storage import storageRepositoryDI
from backend.schemas import NoteForget, NoteForgotten, NoteMove, NoteMoved
from backend.services import ServiceImpl
from backend.services.notes import noteServiceDI

if TYPE_CHECKING:
    from collections.abc import Sequence
    from uuid import UUID


class DecisionService(ServiceImpl[NoteDecisionRepository]):
    repository: noteDecisionRepositoryDI
    pins: notePinRepositoryDI
    notes: noteServiceDI
    jobs: jobRepositoryDI
    transcripts: rawTranscriptRepositoryDI
    storage: storageRepositoryDI

    async def listed(self) -> Sequence[NoteDecision]:
        return await self.repository.all_decisions()

    async def forget(self, payload: NoteForget) -> list[NoteForgotten]:
        return [await self.one(path, payload) for path in payload.paths]

    async def one(self, path: str, payload: NoteForget) -> NoteForgotten:
        if self.notes.repository.resolve(path) is None:
            return NoteForgotten(path=path, removed=False, raw_removed=False)
        note = self.notes.retrieve(path)
        agent = str(note.source.get("agent") or "")
        device = str(note.source.get("device") or "")
        session_id = str(note.source.get("session_id") or "")
        if not session_id:
            return NoteForgotten(path=path, removed=self.notes.repository.delete(path), raw_removed=False)

        job = await self.jobs.find_by_session(agent, device, session_id, lock=True)
        held = await self.repository.of(agent, device, session_id)
        decision = held or NoteDecision(agent=agent, device=device, session_id=session_id, verdict=payload.verdict)
        decision.verdict = NoteVerdict.PURGE if NoteVerdict.PURGE in (decision.verdict, payload.verdict) else payload.verdict
        decision.reason = payload.reason
        if job is not None:
            job.note_path = None
            job.transcript = None
            if job.status in {JobStatus.PENDING, JobStatus.CLAIMED}:
                job.status = JobStatus.DONE
                job.completed_at = datetime.now(UTC)
                job.claimed_at = job.claimed_by = job.claim_token = None
            self.repository.session.add(job)
        await self.repository.save(decision)
        removed = self.notes.repository.delete(path)
        self.notes.forget_session(agent, device, session_id)
        purged = await self.purge(agent, device, session_id) if payload.verdict is NoteVerdict.PURGE else False
        return NoteForgotten(path=path, removed=removed, raw_removed=purged)

    async def purge(self, agent: str, device: str, session_id: str) -> bool:
        transcript = await self.transcripts.find_by_session(agent, device, session_id)
        if transcript is not None:
            self.storage.remove(transcript.relative_path)
            await self.repository.session.delete(transcript)
        if (job := await self.jobs.find_by_session(agent, device, session_id)) is not None:
            await self.repository.session.delete(job)
        await self.repository.session.commit()
        return transcript is not None

    async def pinned(self) -> Sequence[NotePin]:
        return await self.pins.all_pins()

    async def move(self, payload: NoteMove, project: str) -> NoteMoved:
        note = self.notes.retrieve(payload.path)
        agent = str(note.source.get("agent") or "")
        device = str(note.source.get("device") or "")
        session_id = str(note.source.get("session_id") or "")
        if session_id:
            held = await self.pins.find(agent, device, session_id, payload.first_request)
            pin = held or NotePin(agent=agent, device=device, session_id=session_id, project=project, first_request=payload.first_request)
            pin.project = project
            pin.last_request = payload.last_request
            await self.pins.save(pin)
        if payload.first_request:
            return NoteMoved(path=payload.path, project=project, moved_to=None)

        wanted = f"{PROJECTS_ROOT}/{project}/{LOG_DIR}/{payload.path.rpartition('/')[2]}"
        if wanted == payload.path:
            return NoteMoved(path=payload.path, project=project, moved_to=payload.path)
        moved = self.notes.relocate(payload.path, wanted, project)
        if moved is not None:
            await self.jobs.relink({payload.path: moved})
        return NoteMoved(path=payload.path, project=project, moved_to=moved)

    async def unpin(self, pin_id: UUID) -> None:
        pin = await self.pins.retrieve_by_id(pin_id)
        await self.pins.session.delete(pin)
        await self.pins.session.commit()

    async def allow(self, decision_id: UUID) -> None:
        decision = await self.repository.retrieve_by_id(decision_id)
        await self.repository.session.delete(decision)
        await self.repository.session.commit()


decisionServiceDI = Annotated[DecisionService, Depends(DecisionService)]  # noqa: N816
