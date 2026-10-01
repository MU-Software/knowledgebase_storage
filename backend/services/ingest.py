from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Annotated

from fastapi import Depends

from backend.consts.notes import named_project, slugify
from backend.errors import ClientError
from backend.models import Job, JobStatus, ProjectInference, RawTranscript, Upload, UploadKind
from backend.repositories.alias import projectAliasRepositoryDI
from backend.repositories.decision import noteDecisionRepositoryDI
from backend.repositories.job import jobRepositoryDI
from backend.repositories.raw import RawTranscriptRepository, rawTranscriptRepositoryDI, uploadRepositoryDI
from backend.repositories.storage import storageRepositoryDI
from backend.schemas import TranscriptRef, TranscriptState, UploadState
from backend.services import ServiceImpl
from backend.services.extract import extractServiceDI

if TYPE_CHECKING:
    from uuid import UUID

TIMESTAMP = re.compile(rb'"timestamp"\s*:\s*"([^"]{10,40})"')


class IngestService(ServiceImpl[RawTranscriptRepository]):
    repository: rawTranscriptRepositoryDI
    uploads: uploadRepositoryDI
    storage: storageRepositoryDI
    jobs: jobRepositoryDI
    decisions: noteDecisionRepositoryDI
    aliases: projectAliasRepositoryDI
    extract: extractServiceDI

    @staticmethod
    def stamps_in(payload: bytes) -> list[datetime]:
        found = []
        for raw in TIMESTAMP.findall(payload):
            try:
                found.append(datetime.fromisoformat(raw.decode()))
            except ValueError:
                continue
        return sorted(found)

    async def filed_under(self, name: str) -> str:
        slug = slugify(named_project(name))
        return await self.aliases.target(slug) or slug

    async def append(self, ref: TranscriptRef, offset: int, payload: bytes) -> TranscriptState:
        if await self.decisions.purged(ref.agent, ref.device, ref.session_id):
            ClientError.NOTE_WAS_PURGED.format_msg(session_id=ref.session_id).raise_()
        transcript = await self.repository.find_by_session(ref.agent, ref.device, ref.session_id)
        if transcript is None:
            transcript = RawTranscript(agent=ref.agent, device=ref.device, session_id=ref.session_id, project_hint=ref.project, cwd=ref.cwd)
            transcript = await self.repository.save(transcript)
        elif ref.project:
            transcript.project_hint = ref.project
            transcript.cwd = ref.cwd or transcript.cwd

        if offset == 0 and self.storage.size(transcript.relative_path):
            self.storage.truncate(transcript.relative_path)
        transcript.byte_size = self.storage.append(transcript.relative_path, payload, offset)

        if stamps := self.stamps_in(payload):
            transcript.started_at = transcript.started_at or stamps[0]
            transcript.ended_at = stamps[-1]
        transcript.digest = self.storage.digest(transcript.relative_path)
        saved = await self.repository.save(transcript)
        return TranscriptState.model_validate(saved, from_attributes=True)

    async def state(self, ref: TranscriptRef) -> TranscriptState:
        transcript = await self.repository.find_by_session(ref.agent, ref.device, ref.session_id)
        if transcript is None:
            ClientError.RESOURCE_NOT_FOUND.format_msg(resource=self.repository.resource).raise_()
        return TranscriptState.model_validate(transcript, from_attributes=True)

    def messages(self, transcript: RawTranscript, limit: int) -> list[dict[str, object]]:
        lines = []
        with self.storage.contain(transcript.relative_path).open(encoding="utf-8", errors="replace") as handle:
            for raw in handle:
                try:
                    lines.append(json.loads(raw))
                except json.JSONDecodeError:
                    continue
                if len(lines) >= limit:
                    break
        return lines

    async def rebuild(self, device: str | None, project: str | None, limit: int, offset: int) -> list[UUID]:
        reopened: list[UUID] = []
        for transcript in await self.repository.rebuildable(device, project, limit, offset):
            job = await self.jobs.find_by_session(transcript.agent, transcript.device, transcript.session_id)
            if job is not None and job.status in {JobStatus.PENDING, JobStatus.CLAIMED}:
                continue
            if await self.decisions.held_back(transcript.agent, transcript.device, transcript.session_id):
                continue
            if not self.extract.read(transcript).segments:
                continue
            if job is None:
                job = Job(
                    agent=transcript.agent,
                    device=transcript.device,
                    session_id=transcript.session_id,
                    project=await self.filed_under(transcript.project_hint),
                    project_inference=ProjectInference.CWD,
                    cwd=transcript.cwd,
                    started_at=transcript.started_at,
                    ended_at=transcript.ended_at,
                )
            job.status = JobStatus.PENDING
            job.priority = 0
            job.attempts = 0
            job.last_error = None
            job.next_attempt_at = None
            job.refined_at = None
            job.claimed_at = None
            job.claimed_by = None
            job.claim_token = None
            job.last_activity_at = transcript.ended_at or datetime.now(UTC)
            reopened.append((await self.jobs.save(job)).id)
        return reopened

    async def start_upload(self, kind: UploadKind) -> UploadState:
        upload = await self.uploads.save(Upload(kind=kind))
        self.storage.truncate(upload.relative_path)
        return UploadState.model_validate(upload, from_attributes=True)

    async def add_part(self, upload_id: UUID, offset: int, payload: bytes) -> UploadState:
        upload = await self.uploads.retrieve_by_id(upload_id, with_for_update=True)
        upload.byte_size = self.storage.append(upload.relative_path, payload, offset)
        upload.part_count += 1
        return UploadState.model_validate(await self.uploads.save(upload), from_attributes=True)

    async def finish_upload(self, upload_id: UUID, digest: str) -> UploadState:
        upload = await self.uploads.retrieve_by_id(upload_id, with_for_update=True)
        found = self.storage.digest(upload.relative_path)
        if digest and digest != found:
            ClientError.UPLOAD_DIGEST_MISMATCH.raise_()
        upload.digest = found
        upload.completed_at = datetime.now(UTC)
        return UploadState.model_validate(await self.uploads.save(upload), from_attributes=True)


ingestServiceDI = Annotated[IngestService, Depends(IngestService)]  # noqa: N816
