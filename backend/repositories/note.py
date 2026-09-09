from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Annotated

from fastapi import Depends

from backend.errors import InvalidNotePathError, ResourceNotFoundError
from backend.repositories import FSRepositoryImpl
from backend.schemas import NoteDetail, NoteSummary

if TYPE_CHECKING:
    from pathlib import Path

logger = logging.getLogger(__name__)


class NoteRepository(FSRepositoryImpl):
    resource = "note"

    def _safe_summary(self, path: Path) -> NoteSummary | None:
        try:
            return NoteSummary.from_markdown_file(path, self.root)
        except Exception:
            logger.warning("skipping unreadable note: %s", path, exc_info=True)
            return None

    def list_summaries(self, project: str | None = None) -> list[NoteSummary]:
        files = self.iter_files() if project is None else self.iter_files(f"projects/{project}")
        return [summary for path in files if (summary := self._safe_summary(path)) is not None]

    def retrieve(self, relative_path: str) -> NoteDetail:
        if (target := self.resolve(relative_path)) is None:
            raise ResourceNotFoundError(self.resource)
        return NoteDetail.from_markdown_file(target, self.root)

    def write(self, relative_path: str, content: str) -> str:
        if self.contain(relative_path) is None or not relative_path.endswith(self.suffix):
            raise InvalidNotePathError(relative_path)
        return super().write(relative_path, content)

    def grep(self, needle: str, limit: int) -> list[NoteSummary]:
        hits: list[NoteSummary] = []
        for path in self.iter_files():
            try:
                matched = needle in path.read_text(encoding="utf-8").lower()
            except (OSError, UnicodeDecodeError):
                logger.warning("skipping unreadable note: %s", path, exc_info=True)
                continue
            if matched and (summary := self._safe_summary(path)) is not None:
                hits.append(summary)
                if len(hits) >= limit:
                    break
        return hits


noteRepositoryDI = Annotated[NoteRepository, Depends(NoteRepository)]  # noqa: N816
