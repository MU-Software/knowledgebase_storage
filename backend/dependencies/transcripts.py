from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Query

from backend.schemas import TranscriptRef


def transcript_ref(
    agent: Annotated[str, Query(min_length=1, max_length=100)],
    device: Annotated[str, Query(min_length=1, max_length=200)],
    session_id: Annotated[str, Query(min_length=1, max_length=200)],
    project: Annotated[str, Query()] = "",
    cwd: Annotated[str | None, Query()] = None,
) -> TranscriptRef:
    return TranscriptRef(agent=agent, device=device, session_id=session_id, project=project, cwd=cwd)


transcriptRefDI = Annotated[TranscriptRef, Depends(transcript_ref)]  # noqa: N816
