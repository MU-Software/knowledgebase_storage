#!/usr/bin/env python3

from __future__ import annotations

import contextlib
import json
import os
import socket
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

API_BASE = os.environ.get("KBSTORE_API_BASE", "http://workbench-nrt:8006")
TIMEOUT_SECONDS = float(os.environ.get("KBSTORE_TIMEOUT", "3"))
AGENT = os.environ.get("KBSTORE_AGENT", "claude-code")
MAX_MESSAGES = int(os.environ.get("KBSTORE_MAX_MESSAGES", "2000"))

NON_PROJECT_DIRS = {Path.home(), Path("/tmp"), Path("/var/tmp"), Path("/")}  # noqa: S108
TEXT_BLOCK_TYPES = {"text", "input_text", "output_text"}


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


def read_transcript(path: Path) -> list[dict]:
    messages: list[dict] = []
    try:
        with path.open(encoding="utf-8") as fp:
            for raw_line in fp:
                line = raw_line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                message = entry.get("message") or entry
                if not isinstance(message, dict) or message.get("role") not in {"user", "assistant"}:
                    continue
                if text := join_content(message.get("content", "")):
                    messages.append({"role": message["role"], "content": text})
    except OSError:
        return []
    return messages[-MAX_MESSAGES:]


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        payload = {}

    cwd = Path(payload.get("cwd") or Path.cwd())
    transcript_path = payload.get("transcript_path")
    messages = read_transcript(Path(transcript_path)) if transcript_path else []
    if not messages:
        return 0

    project, inference = infer_project(cwd)
    body = json.dumps(
        {
            "agent": AGENT,
            "model": payload.get("model") or os.environ.get("KBSTORE_MODEL"),
            "device": os.environ.get("KBSTORE_DEVICE") or socket.gethostname(),
            "session_id": payload.get("session_id") or "unknown",
            "project": project,
            "project_inference": inference,
            "cwd": cwd.as_posix(),
            "transcript": messages,
        },
        ensure_ascii=False,
    ).encode()

    request = urllib.request.Request(  # noqa: S310
        f"{API_BASE}/api/jobs",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with contextlib.suppress(urllib.error.URLError, OSError, TimeoutError):
        urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS).close()  # noqa: S310
    return 0


if __name__ == "__main__":
    sys.exit(main())
