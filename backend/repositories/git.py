from __future__ import annotations

import subprocess
from typing import TYPE_CHECKING, Annotated

from fastapi import Depends
from sqlmodel import col, select

from backend.dependencies import storageDirDI
from backend.errors import ClientError
from backend.models import GitNetwork, GitSource
from backend.repositories import DBRepositoryImpl, RepositoryImpl

if TYPE_CHECKING:
    from collections.abc import Sequence
    from uuid import UUID

TIMEOUT_SECONDS = 120
OUTPUT_CHARS = 6000
EMPTY_TREE = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"
LISTED_FILES = 60
ENVIRONMENT = {"GIT_NO_LAZY_FETCH": "1", "GIT_TERMINAL_PROMPT": "0", "PATH": "/usr/bin:/bin:/usr/local/bin"}
PROMISOR_CONFIG = (
    ("core.repositoryformatversion", "1"),
    ("extensions.partialClone", "origin"),
    ("remote.origin.url", "kbstore://uploaded"),
    ("remote.origin.promisor", "true"),
    ("remote.origin.fetch", "+refs/*:refs/*"),
    ("core.logAllRefUpdates", "always"),
    ("gc.reflogExpire", "never"),
    ("gc.auto", "0"),
)


def clip(text: str) -> str:
    return text if len(text) <= OUTPUT_CHARS else text[:OUTPUT_CHARS] + f"\n… truncated ({len(text)} chars)"


class GitRepository(RepositoryImpl):
    root: storageDirDI

    resource = "git network"

    def run(self, relative_path: str, *args: str, check: bool = True) -> str:
        target = (self.root / relative_path).resolve()
        if not target.is_relative_to(self.root):
            ClientError.INVALID_STORAGE_PATH.format_msg(path=relative_path).raise_()
        done = subprocess.run(  # noqa: S603
            ["git", "--git-dir", target.as_posix(), *args],  # noqa: S607
            capture_output=True,
            text=True,
            timeout=TIMEOUT_SECONDS,
            check=False,
            env=ENVIRONMENT,
        )
        if done.returncode != 0:
            if check:
                ClientError.GIT_COMMAND_FAILED.format_msg(command=args[0] if args else "", detail=done.stderr.strip()[:200]).raise_()
            return ""
        return done.stdout

    def clipped(self, relative_path: str, *args: str) -> str:
        return clip(self.run(relative_path, *args, check=False))

    def exists(self, relative_path: str) -> bool:
        return (self.root / relative_path / "HEAD").is_file()

    def create(self, relative_path: str) -> None:
        target = self.root / relative_path
        target.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(  # noqa: S603
            ["git", "init", "--bare", "--quiet", target.as_posix()],  # noqa: S607
            check=True,
            timeout=TIMEOUT_SECONDS,
            capture_output=True,
            env=ENVIRONMENT,
        )
        for key, value in PROMISOR_CONFIG:
            self.run(relative_path, "config", key, value)

    def ingest(self, relative_path: str, bundle: str, namespace: str) -> dict[str, str]:
        self.run(relative_path, "bundle", "verify", bundle)
        self.run(relative_path, "fetch", "--force", bundle, f"refs/*:{namespace}/*")
        return self.refs(relative_path, namespace)

    def refs(self, relative_path: str, namespace: str = "refs") -> dict[str, str]:
        listed = self.run(relative_path, "for-each-ref", "--format=%(refname) %(objectname)", namespace, check=False)
        return dict(line.split(" ", 1) for line in listed.splitlines() if " " in line)

    def tip(self, relative_path: str) -> str:
        found = self.run(relative_path, "rev-list", "-1", "--exclude=refs/snapshots/*", "--all", check=False).split()
        return found[0] if found else ""

    def snapshots(self, relative_path: str) -> list[str]:
        return sorted(self.refs(relative_path, "refs/snapshots").values())

    def base_of(self, relative_path: str, ref: str) -> str:
        return self.run(relative_path, "rev-parse", "--verify", "--quiet", f"{ref}^", check=False).strip() or EMPTY_TREE

    def roots(self, relative_path: str) -> list[str]:
        return sorted(set(self.run(relative_path, "rev-list", "--max-parents=0", "--all", check=False).split()))

    def overview(self, relative_path: str) -> str:
        scope = ["--exclude=refs/snapshots/*", "--all"]
        commits = self.clipped(relative_path, "log", *scope, "--format=%h %ad %s", "--date=iso", "-n", "40").strip()
        touched = sorted(set(self.run(relative_path, "log", *scope, "--format=", "--name-only", "-n", "40", check=False).split()))
        parts = [f"commits ({len(commits.splitlines())} newest first):\n{commits or 'none'}"]
        if touched:
            shown = "\n".join(touched[:LISTED_FILES])
            rest = len(touched) - LISTED_FILES
            parts.append(f"files those commits touched ({len(touched)}):\n{shown}" + (f"\n… {rest} more" if rest > 0 else ""))
        for name in self.snapshots(relative_path):
            stat = self.run(relative_path, "diff", "--stat", self.base_of(relative_path, name), name, check=False).strip()
            parts.append(f"uncommitted work captured in a snapshot:\n{stat}" if stat else "")
        return "\n\n".join(part for part in parts if part)


class GitNetworkRepository(DBRepositoryImpl[GitNetwork]):
    model = GitNetwork
    resource = "git network"

    async def find_by_roots(self, roots: list[str]) -> GitNetwork | None:
        wanted = set(roots)
        for network in (await self.session.exec(select(GitNetwork))).all():
            if wanted & set(network.roots):
                return network
        return None


class GitSourceRepository(DBRepositoryImpl[GitSource]):
    model = GitSource
    resource = "git source"

    async def find(self, device: str, path: str) -> GitSource | None:
        query = select(GitSource).where(col(GitSource.device) == device, col(GitSource.path) == path)
        return (await self.session.exec(query)).first()

    async def every(self) -> Sequence[GitSource]:
        return (await self.session.exec(select(GitSource))).all()

    async def list_in(self, network_id: UUID) -> Sequence[GitSource]:
        return (await self.session.exec(select(GitSource).where(col(GitSource.network_id) == network_id))).all()


gitRepositoryDI = Annotated[GitRepository, Depends(GitRepository)]  # noqa: N816
gitNetworkRepositoryDI = Annotated[GitNetworkRepository, Depends(GitNetworkRepository)]  # noqa: N816
gitSourceRepositoryDI = Annotated[GitSourceRepository, Depends(GitSourceRepository)]  # noqa: N816
