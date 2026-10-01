from __future__ import annotations

import shutil
from typing import TYPE_CHECKING, Annotated

from fastapi import Depends

from backend.consts.notes import PROJECTS_ROOT, RESERVED_SEGMENTS
from backend.errors import ClientError
from backend.repositories import FSRepositoryImpl

if TYPE_CHECKING:
    from pathlib import Path


class ProjectRepository(FSRepositoryImpl):
    resource = "project"

    @property
    def base(self) -> Path:
        return self.root / PROJECTS_ROOT

    def directory(self, project_path: str) -> Path | None:
        target = self.contain(f"{PROJECTS_ROOT}/{project_path}")
        return target if target is not None and target.is_dir() else None

    def require(self, project_path: str) -> Path:
        if (target := self.directory(project_path)) is None:
            ClientError.RESOURCE_NOT_FOUND.format_msg(resource=self.resource).raise_()
        return target

    def slugs(self) -> list[str]:
        if not self.base.is_dir():
            return []
        return sorted(path.name for path in self.base.iterdir() if path.is_dir() and path.name not in RESERVED_SEGMENTS)

    def relative(self, path: Path) -> str:
        return path.relative_to(self.root).as_posix()

    def files(self, project_path: str) -> list[Path]:
        directory = self.directory(project_path)
        return sorted(directory.rglob(f"*{self.suffix}")) if directory is not None else []

    def vacant(self, destination: Path, suffix: str) -> Path:
        if not destination.exists():
            return destination
        stem = destination.name.removesuffix(self.suffix)
        candidates = (f"{stem}.{suffix}{'' if attempt == 0 else f'-{attempt}'}{self.suffix}" for attempt in range(100))
        return next(path for name in candidates if not (path := destination.with_name(name)).exists())

    def move_file(self, source: Path, destination: Path) -> tuple[str, str]:
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(source, destination)
        return self.relative(source), self.relative(destination)

    def copy_file(self, source: Path, destination: Path) -> str:
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, destination)
        return self.relative(destination)

    def remove(self, project_path: str) -> int:
        directory = self.require(project_path)
        removed = len(self.files(project_path))
        shutil.rmtree(directory)
        return removed


projectRepositoryDI = Annotated[ProjectRepository, Depends(ProjectRepository)]  # noqa: N816
