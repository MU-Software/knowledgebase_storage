#!/usr/bin/env python3

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import socket
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterator

API_BASE = os.environ.get("KBSTORE_API_BASE", "http://127.0.0.1:8006")
API_KEY = os.environ.get("KBSTORE_API_KEY")
TIMEOUT_SECONDS = float(os.environ.get("KBSTORE_TIMEOUT", "3"))
MAX_MESSAGES = int(os.environ.get("KBSTORE_MAX_MESSAGES", "2000"))
STATE_DIR = Path(os.environ.get("KBSTORE_STATE_DIR") or Path.home() / ".cache" / "kbstore")

NON_PROJECT_DIRS = {Path.home(), Path("/tmp"), Path("/var/tmp"), Path("/")}  # noqa: S108
TEXT_BLOCK_TYPES = {"text", "input_text", "output_text"}
ROLES = {"user", "assistant"}
HOOK_ERRORS = (OSError, ValueError, KeyError, TypeError)


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


def infer_project(cwd: Path) -> tuple[str, str]:
    if remote := _git(cwd, "remote", "get-url", "origin"):
        name = remote.rstrip("/").rsplit("/", 1)[-1]
        return name.removesuffix(".git"), "remote"

    if toplevel := _git(cwd, "rev-parse", "--show-toplevel"):
        return Path(toplevel).name, "worktree"

    if cwd.resolve() in NON_PROJECT_DIRS:
        return "_scratch", "scratch"

    return cwd.name, "cwd"


def block_text(block: object) -> str:
    if isinstance(block, str):
        return block
    if isinstance(block, dict) and block.get("type") in TEXT_BLOCK_TYPES:
        return str(block.get("text", ""))
    return ""


def join_content(content: object) -> str:
    parts = content if isinstance(content, list) else [content]
    return "".join(block_text(p) for p in parts).strip()


def read_lines(path: Path) -> Iterator[dict]:
    with path.open(encoding="utf-8") as fp:
        for line in fp:
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(entry, dict):
                yield entry


def is_subagent(meta: dict) -> bool:
    return meta.get("thread_source", "user") != "user" or isinstance(meta.get("source"), dict)


def message_of(entry: dict) -> dict | None:
    payload = entry.get("payload")
    if entry.get("type") == "response_item" and isinstance(payload, dict) and payload.get("type") == "message":
        return payload
    message = entry.get("message")
    return message if isinstance(message, dict) else None


def read_transcript(path: Path) -> tuple[str, str | None, list[dict]]:
    agent, thread_id, messages = "claude-code", None, []
    try:
        for entry in read_lines(path):
            if entry.get("type") == "session_meta" and thread_id is None:
                payload = entry.get("payload")
                meta = payload if isinstance(payload, dict) else {}
                agent, thread_id = "codex", meta.get("id")
                if is_subagent(meta):
                    return agent, thread_id, []
            message = message_of(entry)
            if message and message.get("role") in ROLES and (text := join_content(message.get("content"))):
                messages.append({"role": message["role"], "content": text})
    except OSError:
        return agent, thread_id, []
    return agent, thread_id, messages[-MAX_MESSAGES:]


def call(method: str, path: str, payload: dict | None = None) -> object | None:
    request = urllib.request.Request(  # noqa: S310
        f"{API_BASE}{path}",
        data=None if payload is None else json.dumps(payload, ensure_ascii=False).encode(),
        headers={"Content-Type": "application/json", **({"X-API-Key": API_KEY} if API_KEY else {})},
        method=method,
    )
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:  # noqa: S310
            return json.loads(response.read())
    except (urllib.error.URLError, OSError, TimeoutError, ValueError):
        return None


def digest(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def memory_dir_of(transcript: Path) -> Path | None:
    return transcript.parent / "memory" if transcript.parent.parent.name == "projects" else None


def state_path_of(memory_dir: Path) -> Path:
    return STATE_DIR / "memory" / f"{memory_dir.parent.name}.json"


def read_memories(memory_dir: Path) -> dict[str, str]:
    return {path.name: path.read_text(encoding="utf-8") for path in sorted(memory_dir.glob("*.md"))}


def load_synced(memory_dir: Path, project: str) -> dict[str, str]:
    try:
        state = json.loads(state_path_of(memory_dir).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return state.get("files", {}) if isinstance(state, dict) and state.get("project") == project else {}


def save_synced(memory_dir: Path, project: str, files: dict[str, str]) -> None:
    state_path = state_path_of(memory_dir)
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps({"project": project, "files": files}), encoding="utf-8")


def push_memories(memory_dir: Path, fields: dict[str, str]) -> None:
    synced = load_synced(memory_dir, fields["project"])
    contents = read_memories(memory_dir)
    digests = {name: digest(text) for name, text in contents.items()}
    files = [{"name": name, "content": contents[name]} for name, value in digests.items() if synced.get(name) != value]
    deleted = [name for name in synced if name not in digests]
    if not files and not deleted:
        return
    if call("PUT", "/api/wiki/memories", {**fields, "files": files, "deleted": deleted}) is not None:
        save_synced(memory_dir, fields["project"], digests)


def pull_memories(memory_dir: Path, project: str) -> None:
    query = urllib.parse.urlencode({"project": project})
    remote = call("GET", f"/api/wiki/memories?{query}")
    if not isinstance(remote, list) or not remote:
        return

    synced = load_synced(memory_dir, project)
    local = read_memories(memory_dir) if memory_dir.is_dir() else {}
    for item in remote:
        name, content = item["name"], item["content"]
        current = local.pop(name, None)
        edited_here = current is not None and digest(current) not in {synced.get(name), digest(content)}
        deleted_here = current is None and name in synced
        if edited_here or deleted_here or Path(name).name != name:
            continue
        if current != content:
            memory_dir.mkdir(parents=True, exist_ok=True)
            (memory_dir / name).write_text(content, encoding="utf-8")
        synced[name] = digest(content)

    for name, current in local.items():
        if synced.get(name) == digest(current):
            (memory_dir / name).unlink()
            del synced[name]
    save_synced(memory_dir, project, synced)


def start_session(payload: dict, cwd: Path, memory_dir: Path | None) -> str:
    project = infer_project(cwd)[0]
    if memory_dir is not None:
        with contextlib.suppress(*HOOK_ERRORS):
            pull_memories(memory_dir, project)

    query = urllib.parse.urlencode({"project": project, "session_id": payload.get("session_id") or "", "memories": str(memory_dir is None).lower()})
    result = call("GET", f"/api/wiki/context?{query}")
    return str(result.get("context") or "") if isinstance(result, dict) else ""


def capture(payload: dict, transcript: Path, cwd: Path) -> dict[str, str] | None:
    agent, thread_id, messages = read_transcript(transcript)
    project, inference = infer_project(cwd)
    fields = {
        "agent": os.environ.get("KBSTORE_AGENT") or agent,
        "device": os.environ.get("KBSTORE_DEVICE") or socket.gethostname(),
        "project": project,
    }
    job = {
        **fields,
        "model": payload.get("model") or os.environ.get("KBSTORE_MODEL"),
        "session_id": thread_id or payload.get("session_id") or "unknown",
        "project_inference": inference,
        "cwd": cwd.as_posix(),
        "transcript": messages,
    }
    if messages and call("POST", "/api/jobs", job) is None:
        return None
    return fields


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        payload = {}

    transcript = Path(payload["transcript_path"]) if payload.get("transcript_path") else None
    cwd = Path(payload.get("cwd") or Path.cwd())
    memory_dir = memory_dir_of(transcript) if transcript is not None else None
    with contextlib.suppress(*HOOK_ERRORS):
        if payload.get("hook_event_name") == "SessionStart":
            if context := start_session(payload, cwd, memory_dir):
                output = {"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": context}}
                sys.stdout.write(json.dumps(output, ensure_ascii=False))
        elif transcript is not None and (fields := capture(payload, transcript, cwd)) is not None and memory_dir is not None:
            push_memories(memory_dir, fields)
    return 0


if __name__ == "__main__":
    sys.exit(main())
