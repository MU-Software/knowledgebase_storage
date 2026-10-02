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
UPLOAD_TIMEOUT_SECONDS = float(os.environ.get("KBSTORE_UPLOAD_TIMEOUT", "60"))
BUNDLE_TIMEOUT_SECONDS = float(os.environ.get("KBSTORE_BUNDLE_TIMEOUT", "30"))
BLOB_LIMIT = os.environ.get("KBSTORE_BLOB_LIMIT", "2m")
CHUNK_BYTES = int(os.environ.get("KBSTORE_CHUNK_BYTES", "1000000"))
FIRST_PUSH_LIMIT_KB = int(os.environ.get("KBSTORE_FIRST_PUSH_LIMIT_KB", str(200 << 10)))
MAX_MESSAGES = int(os.environ.get("KBSTORE_MAX_MESSAGES", "2000"))
STATE_DIR = Path(os.environ.get("KBSTORE_STATE_DIR") or Path.home() / ".cache" / "kbstore")

NON_PROJECT_DIRS = {Path.home(), Path("/tmp"), Path("/var/tmp"), Path("/")}  # noqa: S108
SCRATCH_PARENTS = (Path.home() / "Documents" / "Codex", Path.home() / "Downloads", Path.home() / "Desktop")
TEXT_BLOCK_TYPES = {"text", "input_text", "output_text"}
ROLES = {"user", "assistant"}
HOOK_ERRORS = (OSError, ValueError, KeyError, TypeError)


def path_is_scratch(cwd: Path) -> bool:
    here = cwd.resolve()
    if here in NON_PROJECT_DIRS:
        return True
    return any(here == parent or parent in here.parents for parent in SCRATCH_PARENTS)


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

    if path_is_scratch(cwd):
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


def send_bytes(method: str, path: str, payload: bytes, content_type: str) -> object | None:
    request = urllib.request.Request(  # noqa: S310
        f"{API_BASE}{path}",
        data=payload,
        headers={"Content-Type": content_type, **({"X-API-Key": API_KEY} if API_KEY else {})},
        method=method,
    )
    try:
        with urllib.request.urlopen(request, timeout=UPLOAD_TIMEOUT_SECONDS) as response:  # noqa: S310
            return json.loads(response.read())
    except (urllib.error.URLError, OSError, TimeoutError, ValueError):
        return None


def transcript_query(fields: dict[str, str], extra: dict[str, object] | None = None) -> str:
    query = {
        "agent": fields["agent"],
        "device": fields["device"],
        "session_id": fields["session_id"],
        "project": fields["project"],
        "cwd": fields.get("cwd") or "",
        **(extra or {}),
    }
    return urllib.parse.urlencode({key: value for key, value in query.items() if value != ""})


def push_transcript(fields: dict[str, str], transcript: Path) -> None:
    held = call("GET", f"/api/raw/transcripts?{transcript_query(fields)}")
    offset = int(held.get("byte_size", 0)) if isinstance(held, dict) else 0
    size = transcript.stat().st_size
    if size == offset:
        return
    if not send_transcript(fields, transcript, offset if offset <= size else 0) and offset:
        send_transcript(fields, transcript, 0)


def send_transcript(fields: dict[str, str], transcript: Path, offset: int) -> bool:
    with transcript.open("rb") as handle:
        handle.seek(offset)
        while block := handle.read(CHUNK_BYTES):
            query = transcript_query(fields, {"offset": offset})
            if send_bytes("PUT", f"/api/raw/transcripts?{query}", block, "application/x-ndjson") is None:
                return False
            offset += len(block)
    return True


def upload_file(path: Path, kind: str) -> str | None:
    started = call("POST", f"/api/raw/uploads?kind={kind}")
    if not isinstance(started, dict):
        return None
    upload_id, offset = started["id"], 0
    with path.open("rb") as handle:
        while block := handle.read(CHUNK_BYTES):
            if send_bytes("PUT", f"/api/raw/uploads/{upload_id}?offset={offset}", block, "application/octet-stream") is None:
                return None
            offset += len(block)
    digest = hashlib.sha256(path.read_bytes()).hexdigest() if path.stat().st_size <= CHUNK_BYTES else ""
    query = urllib.parse.urlencode({"digest": digest}) if digest else ""
    return upload_id if call("POST", f"/api/raw/uploads/{upload_id}/complete?{query}") is not None else None


def bundle_of(cwd: Path, name: str, revisions: list[str]) -> Path | None:
    target = STATE_DIR / "bundles" / f"{name}.bundle"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.unlink(missing_ok=True)
    try:
        done = subprocess.run(  # noqa: S603
            ["git", "bundle", "create", target.as_posix(), f"--filter=blob:limit={BLOB_LIMIT}", *revisions],  # noqa: S607
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=BUNDLE_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return target if done.returncode == 0 and target.is_file() and target.stat().st_size else None


def small_enough(repo: Path) -> bool:
    counted = _git(repo, "count-objects", "-v") or ""
    sizes = dict(line.split(": ", 1) for line in counted.splitlines() if ": " in line)
    try:
        total = int(sizes.get("size-pack", "0")) + int(sizes.get("size", "0"))
    except ValueError:
        return False
    return total <= FIRST_PUSH_LIMIT_KB


def push_git(fields: dict[str, str], cwd: Path) -> None:
    toplevel = _git(cwd, "rev-parse", "--show-toplevel")
    if toplevel is None:
        return
    repo = Path(toplevel)
    known = call("GET", "/api/git/sources?" + urllib.parse.urlencode({"device": fields["device"], "path": repo.as_posix()}))
    if not isinstance(known, dict) and not small_enough(repo):
        return

    every = sorted(set(known.get("refs", {}).values())) if isinstance(known, dict) else []
    tips = [tip for tip in every if run_git(repo, "cat-file", "-e", f"{tip}^{{commit}}")[0]]
    heads = (_git(repo, "rev-parse", "--branches", "--tags", "HEAD") or "").split()
    if any(head not in tips for head in heads):
        revisions = ["--branches", "--tags", "HEAD", *[f"^{tip}" for tip in tips]]
        if (bundle := bundle_of(repo, "history", revisions)) and (upload := upload_file(bundle, "git_bundle")):
            call(
                "POST",
                "/api/git/sources",
                {
                    "upload_id": upload,
                    "device": fields["device"],
                    "path": repo.as_posix(),
                    "remote": _git(repo, "remote", "get-url", "origin"),
                    "roots": (_git(repo, "rev-list", "--max-parents=0", "--all") or "").split(),
                },
            )
    push_snapshot(fields, repo)


def run_git(repo: Path, *args: str, environment: dict[str, str] | None = None) -> tuple[bool, str]:
    try:
        done = subprocess.run(  # noqa: S603
            ["git", *args],  # noqa: S607
            cwd=repo,
            env=environment,
            capture_output=True,
            text=True,
            timeout=BUNDLE_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return False, ""
    return done.returncode == 0, done.stdout.strip()


def push_snapshot(fields: dict[str, str], repo: Path) -> None:
    listed, dirty = run_git(repo, "status", "--porcelain")
    if not listed or not dirty:
        return
    index = STATE_DIR / "snapshot-index"
    index.parent.mkdir(parents=True, exist_ok=True)
    index.unlink(missing_ok=True)
    environment = {**os.environ, "GIT_INDEX_FILE": index.as_posix()}
    born = run_git(repo, "rev-parse", "--verify", "--quiet", "HEAD")[0]
    for args in (("read-tree", "HEAD") if born else ("read-tree", "--empty"), ("add", "-A")):
        if not run_git(repo, *args, environment=environment)[0]:
            return
    written, tree = run_git(repo, "write-tree", environment=environment)
    if not written:
        return
    marker = STATE_DIR / "snapshots" / f"{slug_of(repo.as_posix())}.txt"
    if marker.is_file() and marker.read_text(encoding="utf-8").strip() == tree:
        return
    parent = ["-p", "HEAD"] if born else []
    made, commit = run_git(repo, "commit-tree", "--no-gpg-sign", tree, *parent, "-m", "kbstore worktree snapshot", environment=environment)
    if not made:
        return

    reference = f"refs/kbstore/snapshot/{slug_of(fields['device'])}"
    if not run_git(repo, "update-ref", reference, commit)[0]:
        return
    bundle = bundle_of(repo, "snapshot", [f"HEAD..{reference}" if born else reference])
    upload = upload_file(bundle, "worktree") if bundle else None
    body = {"upload_id": upload, "device": fields["device"], "path": repo.as_posix(), "snapshot": True}
    if upload and call("POST", "/api/git/sources", body) is not None:
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text(tree, encoding="utf-8")
    run_git(repo, "update-ref", "-d", reference)


def slug_of(value: str) -> str:
    kept = "".join(char if char.isalnum() or char in "-_" else "-" for char in value)
    return kept.strip("-").lower() or "unknown"


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
        "session_id": thread_id or payload.get("session_id") or "unknown",
        "cwd": cwd.as_posix(),
    }
    push_transcript(fields, transcript)
    job = {
        **{key: value for key, value in fields.items() if key != "cwd"},
        "model": payload.get("model") or os.environ.get("KBSTORE_MODEL"),
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
        elif transcript is not None and (fields := capture(payload, transcript, cwd)) is not None:
            if memory_dir is not None:
                push_memories(memory_dir, fields)
            with contextlib.suppress(*HOOK_ERRORS):
                push_git(fields, cwd)
    return 0


if __name__ == "__main__":
    sys.exit(main())
