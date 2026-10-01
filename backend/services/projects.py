from __future__ import annotations

from collections import Counter, defaultdict
from typing import TYPE_CHECKING, Annotated

from fastapi import Depends

from backend.consts.notes import ARCHIVE_DIR, LOG_DIR, MEMORY_DIR, OVERVIEW_FILE, PROJECTS_ROOT, UNFILED, slugify
from backend.errors import ClientError
from backend.models import Job, JobKind, LinkKind, background_session
from backend.repositories.alias import projectAliasRepositoryDI
from backend.repositories.decision import notePinRepositoryDI
from backend.repositories.entry import projectEntryRepositoryDI
from backend.repositories.job import jobRepositoryDI
from backend.repositories.link import projectLinkRepositoryDI
from backend.repositories.project import ProjectRepository, projectRepositoryDI
from backend.schemas import ProjectDeleteResult, ProjectMerge, ProjectMergeResult, ProjectNode
from backend.services import ServiceImpl
from backend.services.notes import noteServiceDI

if TYPE_CHECKING:
    from pathlib import Path


class Absorbed:
    def __init__(self) -> None:
        self.moves: dict[str, str] = {}
        self.archived: list[str] = []
        self.merging: list[str] = []


class ProjectService(ServiceImpl[ProjectRepository]):
    repository: projectRepositoryDI
    aliases: projectAliasRepositoryDI
    jobs: jobRepositoryDI
    links: projectLinkRepositoryDI
    entries: projectEntryRepositoryDI
    pins: notePinRepositoryDI
    notes: noteServiceDI

    async def resolve(self, name: str) -> str:
        slug = slugify(name)
        return await self.aliases.target(slug) or slug

    async def require(self, name: str) -> str:
        slug = await self.resolve(name)
        self.repository.require(slug)
        return slug

    async def tree(self) -> list[ProjectNode]:
        summaries = self.notes.list_notes()
        counts = Counter(summary.project for summary in summaries)
        overviews = {summary.project: summary.path for summary in summaries if summary.source.get("kind") == "overview"}
        unconfirmed = {summary.project for summary in summaries if summary.source.get("project_inference", "remote") != "remote"}

        aliases: dict[str, list[str]] = defaultdict(list)
        for alias in await self.aliases.all():
            aliases[alias.slug].append(alias.source)

        nodes = [
            ProjectNode(
                name=slug,
                note_count=counts[slug],
                unconfirmed=slug in unconfirmed,
                aliases=sorted(aliases[slug]),
                overview=overviews.get(slug),
            )
            for slug in self.repository.slugs()
        ]
        if counts[UNFILED]:
            nodes.append(ProjectNode(name=UNFILED, note_count=counts[UNFILED], unconfirmed=UNFILED in unconfirmed))
        return nodes

    async def merge(self, payload: ProjectMerge) -> ProjectMergeResult:
        source = await self.require(payload.source)
        target = await self.require(payload.target)
        if source == target:
            ClientError.INVALID_PROJECT_MERGE.raise_()

        absorbed = await self._absorb(source, target)
        await self.jobs.relink(absorbed.moves)
        return ProjectMergeResult(project=target, moved=len(absorbed.moves), archived=absorbed.archived, merging=absorbed.merging)

    async def _absorb(self, source: str, target: str) -> Absorbed:
        directory = self.repository.require(source)
        destination = self.repository.base / target

        absorbed = await self._merge_memories(directory, destination, source, target)
        own = self._move_notes(directory, destination, source)
        absorbed.moves |= own
        await self.jobs.carry_memory_merges(source, target, self.memory_base(target))

        for relative in own.values():
            self.notes.refile(relative, target)

        self.repository.remove(source)
        await self.jobs.drop_background(source, JobKind.PROJECT_OVERVIEW)
        await self.jobs.drop_pairs(source)
        await self.aliases.redirect(source, target)
        await self.pins.carry(source, target)
        await self.jobs.rename_project(source, target)
        await self.carry_links(source, target)
        await self.carry_entry(source, target)
        await self.request_overview(target)
        return absorbed

    def _move_notes(self, directory: Path, destination: Path, source: str) -> dict[str, str]:
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
            old, new = self.repository.move_file(path, self.repository.vacant(wanted, source))
            moves[old] = new
        return moves

    async def _merge_memories(self, directory: Path, destination: Path, source: str, target: str) -> Absorbed:
        absorbed = Absorbed()
        held = directory / MEMORY_DIR

        for path in sorted(held.glob(f"*{self.repository.suffix}")) if held.is_dir() else []:
            wanted = destination / MEMORY_DIR / path.name
            if not wanted.exists():
                old, new = self.repository.move_file(path, wanted)
                absorbed.moves[old] = new
                self.notes.refile(new, target)
                continue

            absorbed.archived += [
                self.repository.copy_file(kept, self.repository.vacant(self._archived(destination, path.name, slug), "again"))
                for kept, slug in ((wanted, target), (path, source))
            ]
            document = self.notes.memory_document(source, path.read_text(encoding="utf-8"))
            await self.jobs.ensure_memory_merge(target, path.name, f"{self.memory_base(target)}/{path.name}", document)
            absorbed.merging.append(path.name)
            path.unlink()
        return absorbed

    async def carry_links(self, source: str, target: str) -> None:
        for link in await self.links.around(source):
            other = link.target if link.source == source else link.source
            if other == target or await self.links.find(target, other) is not None:
                await self.links.session.delete(link)
                continue
            if link.source == source:
                link.source = target
            else:
                link.target = target
            self.links.session.add(link)
        await self.links.session.commit()
        for link in await self.links.around(target):
            if link.kind is LinkKind.PART_OF and await self.links.would_cycle(link.source, link.target):
                link.kind = LinkKind.RELATED
                self.links.session.add(link)
        await self.links.session.commit()

    async def carry_entry(self, source: str, target: str) -> None:
        entry = await self.entries.find(source)
        if entry is None:
            return
        holder = await self.entries.find(target)
        if holder is None:
            entry.slug = target
            await self.entries.save(entry)
            return
        known = {str(source_of.get("path")) for source_of in holder.sources}
        holder.sources = [*holder.sources, *(row for row in entry.sources if str(row.get("path")) not in known)]
        self.entries.session.add(holder)
        await self.entries.session.delete(entry)
        await self.entries.session.commit()

    @staticmethod
    def memory_base(project: str) -> str:
        return f"{PROJECTS_ROOT}/{project}/{MEMORY_DIR}"

    def _archived(self, destination: Path, name: str, slug: str) -> Path:
        stem = name.removesuffix(self.repository.suffix)
        return destination / ARCHIVE_DIR / MEMORY_DIR / f"{stem}.{slug}{self.repository.suffix}"

    async def request_overview(self, name: str) -> Job | None:
        slug = await self.resolve(name)
        if not self.notes.recent_logs(slug, 1):
            self.notes.forget_overview(slug)
            await self.jobs.drop_background(slug, JobKind.PROJECT_OVERVIEW)
            return None
        return await self.jobs.ensure_background(JobKind.PROJECT_OVERVIEW, background_session(JobKind.PROJECT_OVERVIEW, slug), slug)

    async def delete(self, name: str) -> ProjectDeleteResult:
        slug = await self.require(name)
        removed = self.repository.remove(slug)
        await self.aliases.forget(slug)
        await self.jobs.delete_project([slug])
        await self.jobs.drop_pairs(slug)
        await self.links.forget([slug])
        await self.entries.forget([slug])
        return ProjectDeleteResult(project=slug, deleted_notes=removed)


projectServiceDI = Annotated[ProjectService, Depends(ProjectService)]  # noqa: N816
