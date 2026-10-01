#!/usr/bin/env python3
"""Backfill importer for Claude Code and Codex history. Standard library only.

Reads a live ~/.claude or ~/.codex directory, or a tar/zip archive of one, and
sends what it finds to the knowledgebase API. Run it once per origin device.
"""

from __future__ import annotations

import argparse
import functools
import hashlib
import json
import os
import subprocess
import sys
import tarfile
import tempfile
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

TEXT_BLOCK_TYPES = {"text", "input_text", "output_text"}
NON_PROJECT_DIRS = {Path.home(), Path("/tmp"), Path("/var/tmp"), Path("/")}  # noqa: S108
SCRATCH_PARENTS = (Path.home() / "Documents" / "Codex", Path.home() / "Downloads", Path.home() / "Desktop")
CODEX_SESSION_PATTERNS = ("sessions/*/*/*/*.jsonl", "archived_sessions/*.jsonl")
CHUNK_BYTES = 16 << 20
BLOB_LIMIT = "2m"


def path_is_scratch(cwd: Path) -> bool:
    here = cwd.resolve()
    if here in NON_PROJECT_DIRS:
        return True
    return any(here == parent or parent in here.parents for parent in SCRATCH_PARENTS)


@dataclass
class Source:
    """A directory or archive we can list and read files from."""

    root: Path
    archive: tarfile.TarFile | zipfile.ZipFile | None = None
    names: list[str] = field(default_factory=list)

    @classmethod
    def open(cls, path: Path) -> Source:
        if path.is_dir():
            return cls(root=path)
        if tarfile.is_tarfile(path):
            archive = tarfile.open(path)
            return cls(root=path, archive=archive, names=archive.getnames())
        if zipfile.is_zipfile(path):
            archive = zipfile.ZipFile(path)
            return cls(root=path, archive=archive, names=archive.namelist())
        message = f"not a directory, tar or zip: {path}"
        raise SystemExit(message)

    def glob(self, pattern: str) -> list[str]:
        if self.archive is None:
            return sorted(str(p.relative_to(self.root)) for p in self.root.glob(pattern))
        return sorted(n for n in self.names if PurePosixPath(n).match(pattern))

    def read(self, name: str) -> bytes:
        if self.archive is None:
            return (self.root / name).read_bytes()
        if isinstance(self.archive, tarfile.TarFile):
            handle = self.archive.extractfile(name)
            return handle.read() if handle else b""
        return self.archive.read(name)

    def lines(self, name: str) -> Iterator[dict]:
        for line in self.read(name).splitlines():
            if not line.strip():
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                continue


@dataclass
class Plan:
    device: str
    pattern: str
    reader: Callable[[Source, str], Session | None]
    priority: int = 0
    min_chars: int = 200


@dataclass
class Session:
    agent: str
    session_id: str
    cwd: str | None
    started_at: str | None
    ended_at: str | None
    messages: list[dict]

    @property
    def text_length(self) -> int:
        return sum(len(str(m["content"])) for m in self.messages)


def block_text(block: object) -> str:
    if isinstance(block, str):
        return block
    if isinstance(block, dict) and block.get("type") in TEXT_BLOCK_TYPES:
        return str(block.get("text", ""))
    return ""


def join_content(content: object) -> str:
    parts = content if isinstance(content, list) else [content]
    return "".join(block_text(p) for p in parts).strip()


def read_claude_session(source: Source, name: str) -> Session | None:
    session_id, cwd, stamps, messages = PurePosixPath(name).stem, None, [], []
    for record in source.lines(name):
        session_id = record.get("sessionId") or session_id
        cwd = cwd or record.get("cwd")
        if stamp := record.get("timestamp"):
            stamps.append(stamp)
        message = record.get("message")
        if not isinstance(message, dict) or message.get("role") not in ("user", "assistant"):
            continue
        if text := join_content(message.get("content")):
            messages.append({"role": message["role"], "content": text})
    if not messages:
        return None
    stamps.sort()
    return Session("claude-code", session_id, cwd, stamps[0], stamps[-1], messages)


def is_subagent(meta: dict) -> bool:
    return meta.get("thread_source", "user") != "user" or isinstance(meta.get("source"), dict)


def read_codex_session(source: Source, name: str) -> Session | None:
    session_id, cwd, stamps, messages = PurePosixPath(name).stem, None, [], []
    meta_seen = False
    for record in source.lines(name):
        payload = record.get("payload") or {}
        if record.get("type") == "session_meta" and not meta_seen:
            if is_subagent(payload):
                return None
            meta_seen = True
            session_id = payload.get("id") or session_id
            cwd = payload.get("cwd") or (payload.get("turn_context") or {}).get("cwd") or cwd
        if stamp := record.get("timestamp"):
            stamps.append(stamp)
        if record.get("type") != "response_item" or payload.get("type") != "message":
            continue
        if payload.get("role") not in ("user", "assistant"):
            continue
        if text := join_content(payload.get("content")):
            messages.append({"role": payload["role"], "content": text})
    if not messages:
        return None
    stamps.sort()
    return Session("codex", session_id, cwd, stamps[0], stamps[-1], messages)


def _git(cwd: Path, *args: str) -> str | None:
    try:
        out = subprocess.run(  # noqa: S603
            ["git", *args],  # noqa: S607
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout.strip() if out.returncode == 0 and out.stdout.strip() else None


@functools.cache
def project_of(cwd: str | None) -> tuple[str, str]:
    path = Path(cwd) if cwd else None
    if path is None:
        return "unfiled", "cwd"
    if path.is_dir():
        if remote := _git(path, "remote", "get-url", "origin"):
            return remote.rstrip("/").rsplit("/", 1)[-1].removesuffix(".git"), "remote"
        if toplevel := _git(path, "rev-parse", "--show-toplevel"):
            return Path(toplevel).name, "worktree"
    if path_is_scratch(path):
        return "_scratch", "scratch"
    return PurePosixPath(cwd).name or "unfiled", "cwd"


class Client:
    def __init__(self, base: str, api_key: str | None, *, dry_run: bool) -> None:
        self.base = base.rstrip("/")
        self.api_key = api_key
        self.dry_run = dry_run

    def send_bytes(self, method: str, path: str, payload: bytes, content_type: str) -> dict | None:
        if self.dry_run:
            return {}
        request = urllib.request.Request(
            f"{self.base}{path}",
            data=payload,
            headers={"Content-Type": content_type, **({"X-API-Key": self.api_key} if self.api_key else {})},
            method=method,
        )
        try:
            with urllib.request.urlopen(request, timeout=600) as response:
                return json.loads(response.read())
        except urllib.error.HTTPError as error:
            sys.stderr.write(f"  ! {method} {path} -> {error.code} {error.read().decode()[:160]}\n")
            return None
        except (OSError, ValueError) as error:
            sys.stderr.write(f"  ! {method} {path} -> {error}\n")
            return None

    def upload(self, kind: str, payload: bytes) -> str | None:
        started = self.read_post(f"/api/raw/uploads?kind={kind}")
        if not isinstance(started, dict) or self.dry_run:
            return "dry-run" if self.dry_run else None
        upload_id, offset = started["id"], 0
        while offset < len(payload):
            block = payload[offset : offset + CHUNK_BYTES]
            if self.send_bytes("PUT", f"/api/raw/uploads/{upload_id}?offset={offset}", block, "application/octet-stream") is None:
                return None
            offset += len(block)
        digest = hashlib.sha256(payload).hexdigest()
        done = self.read_post(f"/api/raw/uploads/{upload_id}/complete?digest={digest}")
        return upload_id if done is not None else None

    def read_post(self, path: str, payload: dict | None = None) -> dict | None:
        if self.dry_run:
            return {}
        request = urllib.request.Request(
            f"{self.base}{path}",
            data=json.dumps(payload, ensure_ascii=False).encode() if payload is not None else b"",
            headers={"Content-Type": "application/json", **({"X-API-Key": self.api_key} if self.api_key else {})},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=600) as response:
                return json.loads(response.read())
        except urllib.error.HTTPError as error:
            sys.stderr.write(f"  ! POST {path} -> {error.code} {error.read().decode()[:160]}\n")
            return None
        except (OSError, ValueError) as error:
            sys.stderr.write(f"  ! POST {path} -> {error}\n")
            return None

    def send(self, method: str, path: str, payload: dict) -> int:
        if self.dry_run:
            return 0
        request = urllib.request.Request(
            f"{self.base}{path}",
            data=json.dumps(payload, ensure_ascii=False).encode(),
            headers={"Content-Type": "application/json", **({"X-API-Key": self.api_key} if self.api_key else {})},
            method=method,
        )
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                return response.status
        except urllib.error.HTTPError as error:
            sys.stderr.write(f"  ! {method} {path} -> {error.code} {error.read().decode()[:160]}\n")
            return error.code
        except OSError as error:
            sys.stderr.write(f"  ! {method} {path} -> {error}\n")
            return 0


def import_memories(source: Source, client: Client, device: str, projects: dict[str, str]) -> tuple[int, int]:
    holders: dict[str, list[str]] = {}
    for name in source.glob("projects/*/memory/*.md"):
        holders.setdefault(PurePosixPath(name).parent.parent.name, []).append(name)

    sent = skipped = 0
    for holder, names in holders.items():
        project = project_of(projects[holder])[0] if holder in projects else holder.rsplit("-", 1)[-1] or "unfiled"
        files = [{"name": PurePosixPath(name).name, "content": source.read(name).decode("utf-8", "replace")} for name in names]
        payload = {"agent": "claude-code", "device": device, "project": project, "files": files}
        if client.dry_run or client.send("PUT", "/api/wiki/memories", payload) == 200:  # noqa: PLR2004
            sent += len(files)
        else:
            skipped += len(files)
    return sent, skipped


def import_sessions(source: Source, client: Client, plan: Plan) -> tuple[int, int, int]:
    device, pattern, reader = plan.device, plan.pattern, plan.reader
    sent = skipped = chars = 0
    for name in source.glob(pattern):
        session = reader(source, name)
        if session is None or session.text_length < plan.min_chars:
            skipped += 1
            continue
        project, inference = project_of(session.cwd)
        chars += session.text_length
        payload = {
            "agent": session.agent,
            "device": device,
            "session_id": session.session_id,
            "project": project,
            "project_inference": inference,
            "cwd": session.cwd,
            "started_at": session.started_at,
            "ended_at": session.ended_at,
            "transcript": session.messages,
        }
        if client.dry_run or client.send("POST", "/api/jobs", payload) in (200, 202):
            sent += 1
        else:
            skipped += 1
    return sent, skipped, chars


def import_raw(source: Source, client: Client, plan: Plan) -> tuple[int, int, int]:
    sent = skipped = megabytes = 0
    for name in source.glob(plan.pattern):
        session = plan.reader(source, name)
        if session is None or session.text_length < plan.min_chars:
            skipped += 1
            continue
        payload = source.read(name)
        query = urllib.parse.urlencode(
            {
                "agent": session.agent,
                "device": plan.device,
                "session_id": session.session_id,
                "project": project_of(session.cwd)[0],
                "cwd": session.cwd or "",
                "offset": 0,
            }
        )
        if client.send_bytes("PUT", f"/api/raw/transcripts?{query}", payload, "application/x-ndjson") is None:
            skipped += 1
            continue
        sent += 1
        megabytes += len(payload)
    return sent, skipped, megabytes


def import_git(client: Client, device: str, directories: set[str]) -> tuple[int, int]:
    sent = skipped = 0
    for directory in sorted(directories):
        repo = Path(directory)
        if not repo.is_dir() or not _git(repo, "rev-parse", "--show-toplevel"):
            continue
        toplevel = Path(_git(repo, "rev-parse", "--show-toplevel") or directory)
        bundle = Path(tempfile.gettempdir()) / f"kbstore-{abs(hash(toplevel.as_posix()))}.bundle"
        bundle.unlink(missing_ok=True)
        made = subprocess.run(  # noqa: S603
            ["git", "bundle", "create", bundle.as_posix(), f"--filter=blob:limit={BLOB_LIMIT}", "--branches", "--tags", "HEAD"],  # noqa: S607
            cwd=toplevel,
            capture_output=True,
            text=True,
            check=False,
        )
        if made.returncode != 0 or not bundle.is_file():
            sys.stderr.write(f"  ! bundle {toplevel} -> {made.stderr.strip()[:160]}\n")
            skipped += 1
            continue
        upload = client.upload("git_bundle", bundle.read_bytes())
        body = {
            "upload_id": upload,
            "device": device,
            "path": toplevel.as_posix(),
            "remote": _git(toplevel, "remote", "get-url", "origin"),
            "roots": (_git(toplevel, "rev-list", "--max-parents=0", "--all") or "").split(),
        }
        if upload and (client.dry_run or client.read_post("/api/git/sources", body) is not None):
            sent += 1
        else:
            skipped += 1
        bundle.unlink(missing_ok=True)
    return sent, skipped


def map_projects(source: Source) -> dict[str, str]:
    """Recover each Claude project folder's working directory from a session's cwd."""
    mapping: dict[str, str] = {}
    for name in source.glob("projects/*/*.jsonl"):
        holder = PurePosixPath(name).parent.name
        if holder in mapping:
            continue
        for record in source.lines(name):
            if cwd := record.get("cwd"):
                mapping[holder] = cwd
                break
    return mapping


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--api-base", default="http://127.0.0.1:8006")
    parser.add_argument("--api-key", default=os.environ.get("KBSTORE_API_KEY"), help="sent as X-API-Key; defaults to $KBSTORE_API_KEY")
    parser.add_argument("--device", required=True, help="name of the machine the history came from")
    parser.add_argument("--claude", type=Path, help="~/.claude directory or an archive of it")
    parser.add_argument("--codex", type=Path, help="~/.codex directory or an archive of it")
    parser.add_argument("--min-chars", type=int, default=200, help="skip sessions with less conversation than this")
    parser.add_argument("--raw", action="store_true", help="upload the original transcript files, not only parsed conversations")
    parser.add_argument("--git", action="store_true", help="upload each project's git history as a filtered bundle")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if not args.claude and not args.codex:
        parser.error("give --claude and/or --codex")

    client = Client(args.api_base, args.api_key, dry_run=args.dry_run)
    mode = "DRY RUN" if args.dry_run else f"-> {args.api_base}"
    print(f"device={args.device}  {mode}")

    directories: set[str] = set()

    if args.claude:
        source = Source.open(args.claude)
        projects = map_projects(source)
        sent, skipped = import_memories(source, client, args.device, projects)
        print(f"  claude memories : {sent} sent, {skipped} skipped")
        plan = Plan(args.device, "projects/*/*.jsonl", read_claude_session, min_chars=args.min_chars)
        if args.raw:
            sent, skipped, size = import_raw(source, client, plan)
            print(f"  claude raw      : {sent} sent, {skipped} skipped, {size / 1048576:.0f} MB")
        sent, skipped, chars = import_sessions(source, client, plan)
        print(f"  claude sessions : {sent} sent, {skipped} skipped, {chars / 1024:.0f} KB text (~{chars / 2000:.0f}k tokens)")
        directories |= {cwd for cwd in projects.values() if cwd}

    if args.codex:
        source = Source.open(args.codex)
        for pattern in CODEX_SESSION_PATTERNS:
            plan = Plan(args.device, pattern, read_codex_session, min_chars=args.min_chars)
            if args.raw:
                sent, skipped, size = import_raw(source, client, plan)
                print(f"  codex {PurePosixPath(pattern).parts[0]} raw : {sent} sent, {skipped} skipped, {size / 1048576:.0f} MB")
            sent, skipped, chars = import_sessions(source, client, plan)
            label = PurePosixPath(pattern).parts[0]
            print(f"  codex {label} : {sent} sent, {skipped} skipped, {chars / 1024:.0f} KB text (~{chars / 2000:.0f}k tokens)")
            for name in source.glob(pattern):
                session = plan.reader(source, name)
                if session is not None and session.cwd:
                    directories.add(session.cwd)

    if args.git:
        sent, skipped = import_git(client, args.device, directories)
        print(f"  git bundles     : {sent} sent, {skipped} skipped")

    return 0


if __name__ == "__main__":
    sys.exit(main())
