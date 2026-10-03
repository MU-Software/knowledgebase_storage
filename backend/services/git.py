from __future__ import annotations

from typing import TYPE_CHECKING, Annotated

from fastapi import Depends

from backend.consts.notes import slugify
from backend.errors import ClientError
from backend.models import GitNetwork, GitSource, WorktreeFile
from backend.repositories.blob import worktreeFileRepositoryDI
from backend.repositories.git import LISTED_FILES, GitRepository, clip, gitNetworkRepositoryDI, gitRepositoryDI, gitSourceRepositoryDI
from backend.repositories.raw import uploadRepositoryDI
from backend.repositories.storage import storageRepositoryDI
from backend.schemas import GitIngestRequest, GitQuery, GitSourceState, WorktreeFilesRequest, WorktreeFilesState
from backend.services import ServiceImpl
from backend.services.blobs import blobServiceDI

if TYPE_CHECKING:
    from uuid import UUID

SNAPSHOT_NAMESPACE = "refs/snapshots"


class GitService(ServiceImpl[GitRepository]):
    repository: gitRepositoryDI
    networks: gitNetworkRepositoryDI
    sources: gitSourceRepositoryDI
    uploads: uploadRepositoryDI
    storage: storageRepositoryDI
    files: worktreeFileRepositoryDI
    blobs: blobServiceDI

    async def carry_roots(self, network: GitNetwork, roots: list[str]) -> GitNetwork:
        if missing := [root for root in roots if root not in network.roots]:
            network.roots = [*network.roots, *missing]
            network = await self.networks.save(network)
        if not self.repository.exists(network.relative_path):
            self.repository.create(network.relative_path)
        return network

    async def network_of(self, roots: list[str], label: str) -> GitNetwork:
        found = await self.networks.find_by_roots(roots) if roots else None
        if found is None:
            found = await self.networks.save(GitNetwork(roots=roots, label=label))
        return await self.carry_roots(found, roots)

    async def source_of(self, device: str, path: str, roots: list[str], remote: str | None) -> tuple[GitSource, GitNetwork]:
        source = await self.sources.find(device, path)
        if source is None:
            network = await self.network_of(roots, path.rsplit("/", 1)[-1])
            return await self.sources.save(GitSource(network_id=network.id, device=device, path=path, remote=remote)), network
        return source, await self.carry_roots(await self.networks.retrieve_by_id(source.network_id), roots)

    async def report_files(self, payload: WorktreeFilesRequest) -> WorktreeFilesState:
        absent = await self.blobs.incomplete([file.digest for file in payload.files])
        source, _ = await self.source_of(payload.device, payload.path, payload.roots, payload.remote)
        held = {file.path: file for file in await self.files.list_by_source(source.id)}
        previous = {file.digest for file in held.values()}
        kept, skipped = [], []
        for reported in payload.files:
            file = held.get(reported.path)
            if reported.digest in absent:
                skipped.append(reported.path)
            elif file is None:
                file = WorktreeFile(source_id=source.id, **reported.model_dump())
            else:
                file.digest, file.byte_size, file.modified_at = reported.digest, reported.byte_size, reported.modified_at
            if file is not None:
                kept.append(file)
        await self.files.replace(source.id, kept)
        await self.blobs.release(previous - {file.digest for file in kept})
        return WorktreeFilesState(source_id=source.id, files=len(kept), skipped=skipped)

    async def large_files(self, network_id: UUID) -> dict[str, tuple[WorktreeFile, str]]:
        newest: dict[str, tuple[WorktreeFile, str]] = {}
        for file, device in await self.files.list_in_network(network_id):
            newest.setdefault(file.path, (file, device))
        return newest

    @staticmethod
    def large_files_text(newest: dict[str, tuple[WorktreeFile, str]]) -> str:
        if not newest:
            return ""
        lines = [
            f"{path}  {file.byte_size / (1 << 20):.1f} MiB  modified {file.modified_at.isoformat(timespec='minutes')} on {device}"
            for path, (file, device) in list(newest.items())[:LISTED_FILES]
        ]
        rest = len(newest) - LISTED_FILES
        heading = f"large files kept outside git ({len(newest)}), readable with inspect_file:\n"
        return heading + "\n".join(lines) + (f"\n… {rest} more" if rest > 0 else "")

    async def inspect(self, network_id: UUID, payload: GitQuery) -> str:
        newest = await self.large_files(network_id)
        if not payload.path:
            return self.large_files_text(newest) or "no large file was uploaded for this repository"
        if payload.path not in newest:
            return f"{payload.path} is not one of the large files; use read_file for files in git"
        file, device = newest[payload.path]
        heading = f"{file.path} as {device} last saw it ({file.modified_at.isoformat(timespec='minutes')}, {file.byte_size} bytes)"
        return f"{heading}\n\n{await self.blobs.inspect(file.digest, payload)}"

    async def ingest(self, payload: GitIngestRequest) -> GitSourceState:
        upload = await self.uploads.retrieve_by_id(payload.upload_id)
        if upload.completed_at is None:
            ClientError.UPLOAD_DIGEST_MISMATCH.raise_()
        bundle = self.storage.contain(upload.relative_path)
        source, network = await self.source_of(payload.device, payload.path, payload.roots, payload.remote)

        namespace = f"{SNAPSHOT_NAMESPACE}/{source.id}" if payload.snapshot else source.namespace
        refs = self.repository.ingest(network.relative_path, bundle.as_posix(), namespace)
        if payload.snapshot:
            source.snapshot_ref = next(iter(refs), None)
        else:
            source.refs = refs
        source.remote = payload.remote or source.remote
        saved = await self.sources.save(source)
        self.storage.remove(upload.relative_path)
        return GitSourceState(
            network_id=network.id,
            source_id=saved.id,
            namespace=namespace,
            refs=saved.refs,
            snapshot_ref=saved.snapshot_ref,
        )

    async def known(self, device: str, path: str) -> GitSourceState:
        source = await self.sources.find(device, path)
        if source is None:
            ClientError.RESOURCE_NOT_FOUND.format_msg(resource=self.sources.resource).raise_()
        network = await self.networks.retrieve_by_id(source.network_id)
        siblings = await self.sources.list_in(network.id)
        return GitSourceState(
            network_id=network.id,
            source_id=source.id,
            namespace=source.namespace,
            refs={name: commit for other in siblings for name, commit in other.refs.items()},
            snapshot_ref=source.snapshot_ref,
        )

    @staticmethod
    def arguments(payload: GitQuery) -> list[str]:
        limit = (
            []
            if payload.command != "log"
            else ["--all", *[f"--since={payload.since}"] * bool(payload.since), *[f"--until={payload.until}"] * bool(payload.until)]
        )
        shape = {
            "log": [
                "log",
                *limit,
                *(["-G", payload.pattern] if payload.pattern else []),
                "--format=%h %ad %s",
                "--date=iso",
                "--name-status",
                "-n",
                "30",
            ],
            "show": ["show", "--format=%h %ad %s%n%b", "--date=iso", payload.rev],
            "grep": ["grep", "-n", "-I", "-E", payload.pattern, payload.rev],
        }[payload.command]
        return [*shape, "--", *([payload.path] if payload.path else [])]

    async def shared_pairs(self) -> set[tuple[str, str]]:
        grouped: dict[UUID, set[str]] = {}
        for source in await self.sources.every():
            grouped.setdefault(source.network_id, set()).add(source.path)
        pairs: set[tuple[str, str]] = set()
        for paths in grouped.values():
            named = sorted({slugify(path.rsplit("/", 1)[-1]) for path in paths})
            pairs |= {(first, second) for first in named for second in named if first < second}
        return pairs

    async def state_of(self, device: str, path: str) -> tuple[str, UUID | None]:
        source = await self.sources.find(device, path) if path else None
        if source is None:
            return "", None
        network = await self.networks.retrieve_by_id(source.network_id)
        return await self.with_large_files(self.repository.overview(network.relative_path), network.id), network.id

    async def with_large_files(self, text: str, network_id: UUID) -> str:
        return "\n\n".join(part for part in (text, self.large_files_text(await self.large_files(network_id))) if part)

    def revision(self, where: str, rev: str) -> str:
        if rev and rev != "HEAD":
            return rev
        return self.repository.tip(where) or next(iter(self.repository.snapshots(where)), "")

    def worktree(self, where: str, path: str) -> str:
        refs = self.repository.snapshots(where)
        if not refs:
            return "no snapshot of uncommitted work was uploaded for this repository"
        scope = ["--", *([path] if path else [])]
        shown = [self.repository.clipped(where, "diff", self.repository.base_of(where, ref), ref, *scope).strip() for ref in refs]
        return "\n\n".join(part for part in shown if part) or "the snapshot shows no uncommitted change"

    def read_file(self, where: str, payload: GitQuery) -> str:
        lines = self.repository.run(where, "show", f"{payload.rev}:{payload.path}", check=False).splitlines()
        chosen = lines[payload.start - 1 : payload.end]
        numbered = "\n".join(f"{number}: {line}" for number, line in enumerate(chosen, payload.start))
        return clip(numbered) or "empty or missing"

    async def query(self, payload: GitQuery) -> str:
        network = await self.networks.retrieve_by_id(payload.network_id)
        if payload.command == "inspect":
            return await self.inspect(network.id, payload)
        if unsafe := [value for value in (payload.rev, payload.path, payload.pattern) if value.startswith("-")]:
            ClientError.INVALID_GIT_ARGUMENT.format_msg(argument=unsafe[0][:40]).raise_()
        where = network.relative_path
        if payload.command in {"overview", "worktree"}:
            shown = self.repository.overview(where) if payload.command == "overview" else self.worktree(where, payload.path)
            return await self.with_large_files(shown, network.id)
        chosen = payload.model_copy(update={"rev": self.revision(where, payload.rev)})
        if not chosen.rev:
            return "this repository holds no commit yet"
        if chosen.command == "read_file":
            return self.read_file(where, chosen)
        return self.repository.clipped(where, *self.arguments(chosen))


gitServiceDI = Annotated[GitService, Depends(GitService)]  # noqa: N816
