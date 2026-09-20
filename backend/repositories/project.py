from __future__ import annotations

import shutil
from typing import TYPE_CHECKING, Annotated

from fastapi import Depends

from backend.consts.notes import PROJECTS_ROOT, RESERVED_SEGMENTS
from backend.errors import ClientError
from backend.repositories import FSRepositoryImpl

if TYPE_CHECKING:
    from collections.abc import Iterator
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

    def _walk(self, base: Path, prefix: str) -> Iterator[str]:
        if not base.is_dir():
            return
        for child in sorted(path for path in base.iterdir() if path.is_dir()):
            if child.name in RESERVED_SEGMENTS:
                continue
            project_path = prefix + child.name
            yield project_path
            yield from self._walk(child, f"{project_path}/")

    def list_paths(self) -> list[str]:
        return list(self._walk(self.base, ""))

    def children(self, project_path: str) -> list[str]:
        return [path for path in self.list_paths() if path.rpartition("/")[0] == project_path]

    def find(self, slug: str) -> str | None:
        return next((path for path in self.list_paths() if path.rpartition("/")[2] == slug), None)

    def relative(self, path: Path) -> str:
        return path.relative_to(self.root).as_posix()

    def own_files(self, project_path: str) -> list[Path]:
        directory = self.directory(project_path)
        if directory is None:
            return []
        files = [path for path in sorted(directory.iterdir()) if path.is_file() and path.name.endswith(self.suffix)]
        for reserved in sorted(RESERVED_SEGMENTS):
            files += sorted((directory / reserved).rglob(f"*{self.suffix}")) if (directory / reserved).is_dir() else []
        return files

    def subtree_files(self, project_path: str) -> list[Path]:
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

    def move_tree(self, source: str, destination: str) -> dict[str, str]:
        origin = self.require(source)
        target = self.base / destination
        if target.exists():
            ClientError.RESOURCE_ALREADY_EXISTS.format_msg(resource=self.resource).raise_()
        moves = {
            self.relative(path): f"{PROJECTS_ROOT}/{destination}/{path.relative_to(origin).as_posix()}" for path in origin.rglob(f"*{self.suffix}")
        }
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(origin, target)
        return moves

    def remove(self, project_path: str) -> int:
        directory = self.require(project_path)
        removed = len(self.subtree_files(project_path))
        shutil.rmtree(directory)
        return removed

    def prune(self, project_path: str) -> None:
        directory = self.directory(project_path)
        if directory is not None and not any(directory.rglob(f"*{self.suffix}")):
            shutil.rmtree(directory)


projectRepositoryDI = Annotated[ProjectRepository, Depends(ProjectRepository)]  # noqa: N816
