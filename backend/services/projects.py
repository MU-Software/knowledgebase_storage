from __future__ import annotations

from collections import Counter, defaultdict
from typing import TYPE_CHECKING, Annotated

from fastapi import Depends

from backend.consts.notes import (
    ARCHIVE_DIR,
    LOG_DIR,
    MEMORY_DIR,
    OVERVIEW_FILE,
    PROJECTS_ROOT,
    RESERVED_SEGMENTS,
    UNFILED,
    is_within,
    parent_of,
    slugify,
)
from backend.errors import ClientError
from backend.models import Job, JobKind, SuggestionKind, background_session
from backend.repositories.alias import projectAliasRepositoryDI
from backend.repositories.job import jobRepositoryDI
from backend.repositories.project import ProjectRepository, projectRepositoryDI
from backend.repositories.suggestion import projectSuggestionRepositoryDI
from backend.schemas import ProjectDeleteResult, ProjectMerge, ProjectMergeResult, ProjectNode, SuggestionPublic
from backend.services import ServiceImpl
from backend.services.notes import noteServiceDI

if TYPE_CHECKING:
    from pathlib import Path
    from uuid import UUID


def leaf_of(project_path: str) -> str:
    return project_path.rpartition("/")[2]


class Absorbed:
    def __init__(self) -> None:
        self.moves: dict[str, str] = {}
        self.archived: list[str] = []
        self.merging: list[str] = []

    def extend(self, other: Absorbed) -> None:
        self.moves |= other.moves
        self.archived += other.archived
        self.merging += other.merging


class ProjectService(ServiceImpl[ProjectRepository]):
    repository: projectRepositoryDI
    aliases: projectAliasRepositoryDI
    jobs: jobRepositoryDI
    suggestions: projectSuggestionRepositoryDI
    notes: noteServiceDI

    async def resolve(self, name: str) -> str:
        slug = slugify(name)
        return await self.aliases.target(slug) or slug

    def path_of(self, slug: str) -> str:
        return self.repository.find(slug) or slug

    async def resolve_path(self, name: str) -> str:
        return self.path_of(await self.resolve(name))

    async def require_path(self, name: str) -> str:
        slug = await self.resolve(name)
        if (path := self.repository.find(slug)) is None:
            ClientError.RESOURCE_NOT_FOUND.format_msg(resource=self.repository.resource).raise_()
        return path

    async def tree(self) -> list[ProjectNode]:
        summaries = self.notes.list_notes()
        counts = Counter(summary.project_path for summary in summaries)
        overviews = {summary.project_path: summary.path for summary in summaries if summary.source.get("kind") == "overview"}
        unconfirmed = {s.project_path for s in summaries if s.source.get("project_inference", "remote") != "remote"}

        aliases: dict[str, list[str]] = defaultdict(list)
        for alias in await self.aliases.all():
            aliases[alias.slug].append(alias.source)

        paths = self.repository.list_paths()
        nodes = [
            ProjectNode(
                path=path,
                name=leaf_of(path),
                parent=parent_of(path),
                depth=path.count("/"),
                note_count=counts[path],
                total_note_count=sum(count for other, count in counts.items() if is_within(other, path)),
                child_count=sum(1 for other in paths if parent_of(other) == path),
                unconfirmed=path in unconfirmed,
                aliases=sorted(aliases[leaf_of(path)]),
                overview=overviews.get(path),
            )
            for path in paths
        ]
        if counts[UNFILED]:
            nodes.append(
                ProjectNode(
                    path=UNFILED,
                    name=UNFILED,
                    parent=None,
                    depth=0,
                    note_count=counts[UNFILED],
                    total_note_count=counts[UNFILED],
                    child_count=0,
                    unconfirmed=UNFILED in unconfirmed,
                ),
            )
        return nodes

    async def merge(self, payload: ProjectMerge) -> ProjectMergeResult:
        source = await self.require_path(payload.source)
        target = await self.require_path(payload.target)
        if source == target or is_within(target, source):
            ClientError.INVALID_PROJECT_MERGE.raise_()

        absorbed = await self._absorb(source, target)
        await self.jobs.relink(absorbed.moves)
        return ProjectMergeResult(
            project=target,
            moved=len(absorbed.moves),
            archived=absorbed.archived,
            merging=absorbed.merging,
        )

    async def _absorb(self, source: str, target: str) -> Absorbed:
        directory = self.repository.require(source)
        destination = self.repository.base / target
        source_slug, target_slug = leaf_of(source), leaf_of(target)
        absorbed = Absorbed()

        for child in sorted(path for path in directory.iterdir() if path.is_dir() and path.name not in RESERVED_SEGMENTS):
            held, taken = f"{source}/{child.name}", f"{target}/{child.name}"
            if self.repository.directory(taken) is not None:
                absorbed.extend(await self._absorb(held, taken))
            else:
                absorbed.moves |= self.repository.move_tree(held, taken)

        own = self._move_own_notes(directory, destination, source_slug)
        absorbed.moves |= own
        absorbed.extend(await self._merge_memories(directory, destination, source, target))
        await self.jobs.carry_memory_merges(source_slug, target_slug, self.memory_base(target))

        for relative in own.values():
            self.notes.retag(relative, target_slug)

        self.repository.remove(source)
        if source_slug != target_slug:
            await self.jobs.drop_background(source_slug, JobKind.PROJECT_OVERVIEW)
            await self.aliases.redirect(source_slug, target_slug)
            await self.jobs.rename_project(source_slug, target_slug)
            await self.suggestions.forget([source_slug])
        await self.request_overview(target_slug)
        return absorbed

    def _move_own_notes(self, directory: Path, destination: Path, source_slug: str) -> dict[str, str]:
        moves: dict[str, str] = {}
        loose = [
            path
            for path in sorted(directory.iterdir())
            if path.is_file() and path.name.endswith(self.repository.suffix) and path.name != OVERVIEW_FILE
        ]
        for reserved in (LOG_DIR, ARCHIVE_DIR):
            held = directory / reserved
            loose += sorted(held.rglob(f"*{self.repository.suffix}")) if held.is_dir() else []

        for path in loose:
            wanted = destination / path.relative_to(directory)
            old, new = self.repository.move_file(path, self.repository.vacant(wanted, source_slug))
            moves[old] = new
        return moves

    async def _merge_memories(self, directory: Path, destination: Path, source: str, target: str) -> Absorbed:
        absorbed = Absorbed()
        held = directory / MEMORY_DIR
        source_slug, target_slug = leaf_of(source), leaf_of(target)

        for path in sorted(held.glob(f"*{self.repository.suffix}")) if held.is_dir() else []:
            wanted = destination / MEMORY_DIR / path.name
            if not wanted.exists():
                old, new = self.repository.move_file(path, wanted)
                absorbed.moves[old] = new
                self.notes.retag(new, target_slug)
                continue

            absorbed.archived += [
                self.repository.copy_file(kept, self.repository.vacant(self._archived(destination, path.name, slug), "again"))
                for kept, slug in ((wanted, target_slug), (path, source_slug))
            ]
            document = self.notes.memory_document(source, path.read_text(encoding="utf-8"))
            await self.jobs.ensure_memory_merge(target_slug, path.name, f"{self.memory_base(target)}/{path.name}", document)
            absorbed.merging.append(path.name)
            path.unlink()
        return absorbed

    @staticmethod
    def memory_base(project_path: str) -> str:
        return f"{PROJECTS_ROOT}/{project_path}/{MEMORY_DIR}"

    def _archived(self, destination: Path, name: str, slug: str) -> Path:
        stem = name.removesuffix(self.repository.suffix)
        return destination / ARCHIVE_DIR / MEMORY_DIR / f"{stem}.{slug}{self.repository.suffix}"

    async def request_overview(self, slug: str) -> Job | None:
        path = self.path_of(slug)
        if not self.notes.recent_logs(path, 1, subtree=False) and not self.repository.children(path):
            self.notes.forget_overview(path)
            await self.jobs.drop_background(slug, JobKind.PROJECT_OVERVIEW)
            return None
        return await self.jobs.ensure_background(JobKind.PROJECT_OVERVIEW, background_session(JobKind.PROJECT_OVERVIEW, slug), slug)

    async def reparent(self, name: str, parent: str | None) -> ProjectNode:
        path = await self.require_path(name)
        slug = leaf_of(path)
        destination = slug if parent is None else f"{await self.require_path(parent)}/{slug}"
        if is_within(parent_of(destination) or "", path):
            ClientError.INVALID_PROJECT_PARENT.raise_()

        if destination != path:
            await self.jobs.relink(self.repository.move_tree(path, destination))
            for affected in (parent_of(path), parent_of(destination)):
                if affected is not None:
                    await self.request_overview(leaf_of(affected))
        return next(node for node in await self.tree() if node.path == destination)

    async def decide_suggestion(self, suggestion_id: UUID, *, applied: bool, reverse: bool = False) -> SuggestionPublic:
        suggestion = await self.suggestions.retrieve_by_id(suggestion_id)
        public = SuggestionPublic.model_validate(suggestion, from_attributes=True)
        source, target = (suggestion.target, suggestion.source) if reverse else (suggestion.source, suggestion.target)
        if applied and suggestion.kind is SuggestionKind.MERGE:
            await self.merge(ProjectMerge(source=source, target=target))
        elif applied:
            await self.reparent(source, target)
        await self.suggestions.decide(suggestion_id, applied=applied)
        return public

    async def delete(self, name: str) -> ProjectDeleteResult:
        path = await self.require_path(name)
        held = [other for other in self.repository.list_paths() if is_within(other, path)]
        removed = self.repository.remove(path)

        slugs = [leaf_of(other) for other in held]
        for slug in slugs:
            await self.aliases.forget(slug)
        await self.jobs.delete_project(slugs)
        await self.suggestions.forget(slugs)
        if (above := parent_of(path)) is not None:
            await self.request_overview(leaf_of(above))
        return ProjectDeleteResult(path=path, deleted_notes=removed, deleted_projects=held)


projectServiceDI = Annotated[ProjectService, Depends(ProjectService)]  # noqa: N816
