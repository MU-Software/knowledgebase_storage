from __future__ import annotations

from typing import TYPE_CHECKING, Annotated

from fastapi import Depends

from backend.consts.notes import slugify
from backend.errors import ClientError
from backend.models import GitNetwork, GitSource
from backend.repositories.git import OUTPUT_CHARS, GitRepository, gitNetworkRepositoryDI, gitRepositoryDI, gitSourceRepositoryDI
from backend.repositories.raw import uploadRepositoryDI
from backend.repositories.storage import storageRepositoryDI
from backend.schemas import GitIngestRequest, GitQuery, GitSourceState
from backend.services import ServiceImpl

if TYPE_CHECKING:
    from uuid import UUID

SNAPSHOT_NAMESPACE = "refs/snapshots"


def clip(text: str) -> str:
    return text if len(text) <= OUTPUT_CHARS else text[:OUTPUT_CHARS] + f"\n… truncated ({len(text)} chars)"


class GitService(ServiceImpl[GitRepository]):
    repository: gitRepositoryDI
    networks: gitNetworkRepositoryDI
    sources: gitSourceRepositoryDI
    uploads: uploadRepositoryDI
    storage: storageRepositoryDI

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

    async def ingest(self, payload: GitIngestRequest) -> GitSourceState:
        upload = await self.uploads.retrieve_by_id(payload.upload_id)
        if upload.completed_at is None:
            ClientError.UPLOAD_DIGEST_MISMATCH.raise_()
        bundle = self.storage.contain(upload.relative_path)

        source = await self.sources.find(payload.device, payload.path)
        if source is None:
            network = await self.network_of(payload.roots, payload.path.rsplit("/", 1)[-1])
            source = await self.sources.save(GitSource(network_id=network.id, device=payload.device, path=payload.path, remote=payload.remote))
        else:
            network = await self.carry_roots(await self.networks.retrieve_by_id(source.network_id), payload.roots)

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
        return self.repository.overview(network.relative_path), network.id

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
        if unsafe := [value for value in (payload.rev, payload.path, payload.pattern) if value.startswith("-")]:
            ClientError.INVALID_GIT_ARGUMENT.format_msg(argument=unsafe[0][:40]).raise_()
        network = await self.networks.retrieve_by_id(payload.network_id)
        where = network.relative_path
        if payload.command == "overview":
            return self.repository.overview(where)
        if payload.command == "worktree":
            return self.worktree(where, payload.path)
        chosen = payload.model_copy(update={"rev": self.revision(where, payload.rev)})
        if not chosen.rev:
            return "this repository holds no commit yet"
        if chosen.command == "read_file":
            return self.read_file(where, chosen)
        return self.repository.clipped(where, *self.arguments(chosen))


gitServiceDI = Annotated[GitService, Depends(GitService)]  # noqa: N816
