#!/usr/bin/env python3
"""Backfill importer for Claude Code and Codex history. Standard library only.

Reads a live ~/.claude or ~/.codex directory, or a tar/zip archive of one, and
sends what it finds to the knowledgebase API. Run it once per origin device.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import tarfile
import urllib.error
import urllib.request
import zipfile
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

TEXT_BLOCK_TYPES = {"text", "input_text", "output_text"}
FRONTMATTER = re.compile(r"\A---\n(.*?)\n---\n(.*)\Z", re.DOTALL)


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


def read_codex_session(source: Source, name: str) -> Session | None:
    session_id, cwd, stamps, messages = PurePosixPath(name).stem, None, [], []
    for record in source.lines(name):
        payload = record.get("payload") or {}
        if record.get("type") == "session_meta":
            session_id = payload.get("session_id") or session_id
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


def project_of(cwd: str | None, fallback: str) -> tuple[str, str]:
    if not cwd:
        return fallback, "cwd"
    name = PurePosixPath(cwd).name
    return (name or fallback), "cwd"


def parse_frontmatter(text: str) -> tuple[dict, str]:
    match = FRONTMATTER.match(text)
    if not match:
        return {}, text
    meta: dict = {}
    stack: list[tuple[int, dict]] = [(0, meta)]
    for line in match.group(1).splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        indent = len(line) - len(line.lstrip())
        while len(stack) > 1 and indent <= stack[-1][0]:
            stack.pop()
        key, _, value = line.strip().partition(":")
        value = value.strip()
        if value:
            stack[-1][1][key.strip()] = value
        else:
            child: dict = {}
            stack[-1][1][key.strip()] = child
            stack.append((indent, child))
    return meta, match.group(2)


def memory_note(meta: dict, body: str, project: str, device: str, relative: str) -> str:
    name = meta.get("name") or PurePosixPath(relative).stem
    description = meta.get("description", "")
    kind = (meta.get("metadata") or {}).get("type", "memory")
    origin = (meta.get("metadata") or {}).get("originSessionId", "")
    tags = [f"project/{project}", f"type/{kind}", f"device/{device}", "imported/memory"]
    lines = [
        "---",
        f"title: {name}",
        "type: note",
        "tags:",
        *[f"- {tag}" for tag in tags],
        "source:",
        "  agent: claude-code",
        f"  device: {device}",
        f"  origin: {relative}",
    ]
    if origin:
        lines.append(f"  session_id: {origin}")
    lines += ["---", "", f"# {name}", ""]
    if description:
        lines += [description, ""]
    return "\n".join(lines) + body.strip() + "\n"


class Client:
    def __init__(self, base: str, *, dry_run: bool) -> None:
        self.base = base.rstrip("/")
        self.dry_run = dry_run

    def send(self, method: str, path: str, payload: dict) -> int:
        if self.dry_run:
            return 0
        request = urllib.request.Request(
            f"{self.base}{path}",
            data=json.dumps(payload, ensure_ascii=False).encode(),
            headers={"Content-Type": "application/json"},
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
    sent = skipped = 0
    for name in source.glob("projects/*/memory/*.md"):
        if PurePosixPath(name).name == "MEMORY.md":
            skipped += 1
            continue
        text = source.read(name).decode("utf-8", "replace")
        meta, body = parse_frontmatter(text)
        holder = PurePosixPath(name).parent.parent.name
        project = projects.get(holder, holder.rsplit("-", 1)[-1] or "unfiled")
        target = f"projects/{project}/memory/{PurePosixPath(name).stem}.md"
        content = memory_note(meta, body, project, device, name)
        if client.dry_run or client.send("PUT", "/api/wiki/notes", {"path": target, "content": content}) in (200, 201):
            sent += 1
        else:
            skipped += 1
    return sent, skipped


def import_sessions(source: Source, client: Client, plan: Plan) -> tuple[int, int, int]:
    device, pattern, reader = plan.device, plan.pattern, plan.reader
    sent = skipped = chars = 0
    for name in source.glob(pattern):
        session = reader(source, name)
        if session is None or session.text_length < plan.min_chars:
            skipped += 1
            continue
        project, inference = project_of(session.cwd, "unfiled")
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


def map_projects(source: Source) -> dict[str, str]:
    """Recover each Claude project folder's real directory name from a session's cwd."""
    mapping: dict[str, str] = {}
    for name in source.glob("projects/*/*.jsonl"):
        holder = PurePosixPath(name).parent.name
        if holder in mapping:
            continue
        for record in source.lines(name):
            if cwd := record.get("cwd"):
                mapping[holder] = PurePosixPath(cwd).name
                break
    return mapping


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--api-base", default="http://workbench-nrt:8006")
    parser.add_argument("--device", required=True, help="name of the machine the history came from")
    parser.add_argument("--claude", type=Path, help="~/.claude directory or an archive of it")
    parser.add_argument("--codex", type=Path, help="~/.codex directory or an archive of it")
    parser.add_argument("--min-chars", type=int, default=200, help="skip sessions with less conversation than this")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if not args.claude and not args.codex:
        parser.error("give --claude and/or --codex")

    client = Client(args.api_base, dry_run=args.dry_run)
    mode = "DRY RUN" if args.dry_run else f"-> {args.api_base}"
    print(f"device={args.device}  {mode}")

    if args.claude:
        source = Source.open(args.claude)
        projects = map_projects(source)
        sent, skipped = import_memories(source, client, args.device, projects)
        print(f"  claude memories : {sent} sent, {skipped} skipped")
        plan = Plan(args.device, "projects/*/*.jsonl", read_claude_session, args.min_chars)
        sent, skipped, chars = import_sessions(source, client, plan)
        print(f"  claude sessions : {sent} sent, {skipped} skipped, {chars / 1024:.0f} KB text (~{chars / 2000:.0f}k tokens)")

    if args.codex:
        source = Source.open(args.codex)
        plan = Plan(args.device, "sessions/*/*/*/*.jsonl", read_codex_session, args.min_chars)
        sent, skipped, chars = import_sessions(source, client, plan)
        print(f"  codex sessions  : {sent} sent, {skipped} skipped, {chars / 1024:.0f} KB text (~{chars / 2000:.0f}k tokens)")

    return 0


if __name__ == "__main__":
    sys.exit(main())
