from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Annotated, Any

from fastapi import Depends

from backend.repositories.storage import StorageRepository, storageRepositoryDI
from backend.services import ServiceImpl

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

    from backend.models import RawTranscript

NOISE = re.compile(r"<(ide_opened_file|ide_selection|system-reminder|local-command-stdout|local-command-caveat)>.*?</\1>", re.DOTALL)
COMMAND = re.compile(r"<command-name>(.*?)</command-name>.*?(?:<command-args>(.*?)</command-args>)?", re.DOTALL)
IDE_CONTEXT = re.compile(r"^# Context from my IDE setup:.*?^## My request[^\n]*:\s*\n", re.DOTALL | re.MULTILINE)
ACTIVE_FILE = re.compile(r"^## Active file: (.+)$", re.MULTILINE)
GOAL = re.compile(r'<codex_internal_context source="goal">.*?<objective>\s*(.*?)\s*</objective>', re.DOTALL)
QUESTION_REPLY = re.compile(r"<send_user_message_question_reply>(.*?)</send_user_message_question_reply>", re.DOTALL)
PATCH_FILE = re.compile(r"^\*\*\* (?:Update|Add|Delete) File: (.+)$", re.MULTILINE)
EXEC_COMMAND = re.compile(r'cmd:\s*"((?:[^"\\]|\\.)*)"')

FILE_TOOLS = {"Edit": "edit", "Write": "write", "MultiEdit": "edit", "NotebookEdit": "edit"}
SKIP_TOOLS = frozenset({"Read", "Grep", "Glob", "ToolSearch", "TodoWrite", "LS"})
CODEX_NOISE = ("# AGENTS.md instructions", "<environment_context>", "<user_instructions>", "<permissions instructions>", "<recommended_plugins>")
ROLES = frozenset({"user", "assistant"})
SLASH_ONLY = re.compile(r"^/[\w:.-]+\s*$")
MIN_REQUEST_CHARS = 20
EDITED = ("edit ", "write ")

TRACE_CHARS = 160
ANSWER_CHARS = 1500
PLAN_CHARS = 400
ERROR_CHARS = 300
MAX_COMMANDS = 20


def flatten(text: object, limit: int = TRACE_CHARS) -> str:
    joined = " ".join(str(text).split())
    return joined if len(joined) <= limit else joined[: limit - 1] + "…"


def clean_request(text: str) -> str:
    stripped = NOISE.sub("", text)
    stripped = COMMAND.sub(lambda found: f"/{found[1].strip().lstrip('/')} {(found[2] or '').strip()}".strip(), stripped)
    return stripped.strip()


@dataclass
class Segment:
    number: int
    request: str
    started_at: str
    lines: list[str] = field(default_factory=list)
    commands: int = 0
    skipped: int = 0

    def add(self, traces: list[str]) -> None:
        kept = []
        for trace in dict.fromkeys(traces):
            if trace.startswith(("edit ", "write ")) or "실패" in trace:
                kept.append(trace)
            elif self.commands < MAX_COMMANDS:
                self.commands += 1
                kept.append(trace)
            else:
                self.skipped += 1
        if kept:
            self.lines.append("도구: " + ", ".join(kept))

    def render(self) -> str:
        tail = [f"(그 밖의 명령 {self.skipped}개 생략)"] if self.skipped else []
        heading = f"## 사용자 요청 #{self.number} ({self.started_at[:16]})"
        return "\n".join([heading, self.request, "", *self.lines, *tail]).strip()


class Reader:
    def __init__(self) -> None:
        self.segments: list[Segment] = []
        self.traces: list[str] = []
        self.cwd = ""
        self.started_at = ""
        self.ended_at = ""

    def flush(self) -> None:
        if self.traces and self.segments:
            self.segments[-1].add(self.traces)
        self.traces.clear()

    def note(self, line: str) -> None:
        self.flush()
        if self.segments and line not in self.segments[-1].lines:
            self.segments[-1].lines.append(line)

    def open(self, request: str, stamp: str) -> None:
        self.flush()
        self.segments.append(Segment(number=len(self.segments) + 1, request=request, started_at=stamp))

    def stamp(self, value: str) -> None:
        if value:
            self.started_at = self.started_at or value
            self.ended_at = value


class ExtractService(ServiceImpl[StorageRepository]):
    repository: storageRepositoryDI

    @staticmethod
    def substantial(reader: Reader) -> bool:
        asked = [" ".join(segment.request.split()) for segment in reader.segments]
        if any(not SLASH_ONLY.match(request) and len(request) >= MIN_REQUEST_CHARS for request in asked):
            return True
        return any(trace.startswith(EDITED) for segment in reader.segments for line in segment.lines for trace in line.split(", "))

    def lines_of(self, path: Path) -> Iterator[dict[str, Any]]:
        with path.open(encoding="utf-8", errors="replace") as handle:
            for raw in handle:
                try:
                    entry = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                if isinstance(entry, dict):
                    yield entry

    def stored(self, transcript: RawTranscript) -> bool:
        return self.repository.contain(transcript.relative_path).is_file()

    def read(self, transcript: RawTranscript) -> Reader:
        path = self.repository.contain(transcript.relative_path)
        if not path.is_file():
            return Reader()
        with path.open(encoding="utf-8", errors="replace") as handle:
            codex = '"session_meta"' in handle.readline()
        return self.read_codex(path) if codex else self.read_claude(path)

    @staticmethod
    def trace_of(name: str, arguments: dict[str, Any], cwd: str) -> str | None:
        if name in SKIP_TOOLS:
            return None
        if name in FILE_TOOLS:
            path = str(arguments.get("file_path") or arguments.get("notebook_path") or "")
            return f"{FILE_TOOLS[name]} {path.removeprefix(cwd + '/')}"
        shapes = {
            "Bash": lambda: f"$ {flatten(arguments.get('description') or arguments.get('command', ''))}",
            "Agent": lambda: f"subagent: {flatten(arguments.get('description') or arguments.get('prompt', ''))}",
            "Task": lambda: f"subagent: {flatten(arguments.get('description') or arguments.get('prompt', ''))}",
            "WebFetch": lambda: f"fetch {arguments.get('url', '')}",
            "WebSearch": lambda: f"search {flatten(arguments.get('query', ''))}",
        }
        if shape := shapes.get(name):
            return shape()
        return f"{name} {flatten(json.dumps(arguments, ensure_ascii=False), 100)}"

    def read_claude(self, path: Path) -> Reader:
        reader = Reader()
        pending: dict[str, tuple[str, dict[str, Any]]] = {}
        for entry in self.lines_of(path):
            reader.cwd = reader.cwd or str(entry.get("cwd") or "")
            reader.stamp(str(entry.get("timestamp") or ""))
            if entry.get("type") == "attachment":
                self.read_attachment(reader, entry)
                continue
            message = entry.get("message")
            if entry.get("isMeta") or not isinstance(message, dict):
                continue
            content = message.get("content")
            blocks = [{"type": "text", "text": content}] if isinstance(content, str) else content or []
            if message.get("role") == "user":
                self.read_claude_user(reader, entry, blocks, pending)
            elif message.get("role") == "assistant" and reader.segments:
                self.read_claude_assistant(reader, blocks, pending)
        reader.flush()
        return reader

    @staticmethod
    def read_attachment(reader: Reader, entry: dict[str, Any]) -> None:
        attachment = entry.get("attachment") or {}
        if attachment.get("type") != "queued_command" or (attachment.get("origin") or {}).get("kind") != "human":
            return
        prompt = attachment.get("prompt")
        text = prompt if isinstance(prompt, str) else "".join(str(part.get("text", "")) for part in prompt or [] if isinstance(part, dict))
        if cleaned := clean_request(text):
            reader.note(f"[작업 중 사용자 추가 요청] {cleaned}")

    def read_claude_user(
        self, reader: Reader, entry: dict[str, Any], blocks: list[dict[str, Any]], pending: dict[str, tuple[str, dict[str, Any]]]
    ) -> None:
        for block in blocks:
            if block.get("type") == "tool_result":
                name, _ = pending.pop(str(block.get("tool_use_id", "")), ("", {}))
                text = self.result_text(block)
                if name == "AskUserQuestion":
                    reader.note(f"[사용자 답변] {flatten(text, ANSWER_CHARS)}")
                elif name == "ExitPlanMode":
                    reader.note(f"[계획 승인 결과] {flatten(text, PLAN_CHARS)}")
                elif block.get("is_error") and name:
                    reader.traces.append(f"{name} 실패: {flatten(text, ERROR_CHARS)}")
            elif block.get("type") == "text" and not entry.get("isCompactSummary") and (text := clean_request(str(block.get("text", "")))):
                reader.open(text, str(entry.get("timestamp") or ""))

    def read_claude_assistant(self, reader: Reader, blocks: list[dict[str, Any]], pending: dict[str, tuple[str, dict[str, Any]]]) -> None:
        for block in blocks:
            kind = block.get("type")
            if kind == "text" and (text := str(block.get("text", "")).strip()):
                reader.note(f"[assistant] {text}")
            elif kind == "tool_use":
                name, arguments = str(block.get("name", "")), block.get("input") or {}
                pending[str(block.get("id", ""))] = (name, arguments)
                if name == "AskUserQuestion":
                    for question in arguments.get("questions", []):
                        options = " / ".join(str(option.get("label", "")) for option in question.get("options", []))
                        reader.note(f"[assistant 질문] {question.get('question')} (선택지: {options})")
                elif name == "ExitPlanMode":
                    reader.note(f"[계획]\n{arguments.get('plan', '')}")
                elif trace := self.trace_of(name, arguments, reader.cwd):
                    reader.traces.append(trace)

    @staticmethod
    def result_text(block: dict[str, Any]) -> str:
        content = block.get("content")
        if isinstance(content, list):
            return "\n".join(str(part.get("text", "")) for part in content if isinstance(part, dict))
        return str(content or "")

    @staticmethod
    def codex_traces(name: str, raw: str, cwd: str) -> list[str]:
        if name == "apply_patch" or "tools." in raw:
            found = [f"edit {path.strip().removeprefix(cwd + '/')}" for path in PATCH_FILE.findall(raw)]
            found += [f"$ {flatten(bytes(command, 'utf-8').decode('unicode_escape', 'ignore'))}" for command in EXEC_COMMAND.findall(raw)]
            if "web__run" in raw:
                found.append("web search")
            return found
        if name in {"exec_command", "shell"}:
            try:
                arguments = json.loads(raw)
            except json.JSONDecodeError:
                return [f"$ {flatten(raw)}"]
            return [f"$ {flatten(arguments.get('cmd') or arguments.get('command') or '')}"]
        if name == "spawn_agent":
            try:
                return [f"subagent: {json.loads(raw).get('task_name', '')}"]
            except json.JSONDecodeError:
                return ["subagent"]
        return []

    @staticmethod
    def codex_answers(text: str) -> list[str] | None:
        found = QUESTION_REPLY.search(text)
        if found is None:
            return None
        try:
            replies = json.loads(found[1])
        except json.JSONDecodeError:
            return [flatten(found[1], ANSWER_CHARS)]
        return [f"{reply.get('question', '')} → {reply.get('answer', '')}" for reply in replies if isinstance(reply, dict)]

    def read_codex(self, path: Path) -> Reader:
        reader = Reader()
        for entry in self.lines_of(path):
            payload = entry.get("payload") or {}
            reader.stamp(str(entry.get("timestamp") or ""))
            if entry.get("type") == "session_meta":
                reader.cwd = str(payload.get("cwd") or reader.cwd)
                continue
            if entry.get("type") != "response_item":
                continue
            if payload.get("type") == "message":
                self.read_codex_message(reader, payload)
            elif payload.get("type") in {"function_call", "custom_tool_call"} and reader.segments:
                self.read_codex_call(reader, payload)
        reader.flush()
        return reader

    def read_codex_message(self, reader: Reader, payload: dict[str, Any]) -> None:
        text = "".join(str(block.get("text", "")) for block in payload.get("content") or [] if isinstance(block, dict)).strip()
        role = payload.get("role")
        if role == "assistant" and text and reader.segments:
            reader.note(f"[assistant] {text}")
            return
        if role != "user" or not text or text.startswith(CODEX_NOISE):
            return
        if text.startswith("<codex_internal_context"):
            goal = GOAL.search(text)
            reader.note(f"[자동 계속] 목표: {flatten(goal[1], PLAN_CHARS)}" if goal else "[자동 계속]")
            return
        active = ""
        if text.startswith("# Context from my IDE setup:"):
            found = ACTIVE_FILE.search(text)
            active = found[1].strip() if found else ""
            text = IDE_CONTEXT.sub("", text, count=1).strip()
        if not text:
            return
        if (answers := self.codex_answers(text)) is not None:
            for answer in answers:
                reader.note(f"[사용자 답변] {flatten(answer, ANSWER_CHARS)}")
            return
        reader.open(clean_request(text), reader.ended_at)
        if active:
            reader.segments[-1].lines.append(f"(활성 파일: {active})")

    def read_codex_call(self, reader: Reader, payload: dict[str, Any]) -> None:
        name = str(payload.get("name", ""))
        raw = str(payload.get("input") if payload.get("type") == "custom_tool_call" else payload.get("arguments") or "")
        if name == "request_user_input_async":
            try:
                questions = json.loads(raw or "{}").get("questions", [])
            except json.JSONDecodeError:
                questions = []
            for question in questions:
                options = " / ".join(str(option) for option in question.get("options", []))
                reader.note(f"[assistant 질문] {question.get('title') or question.get('question', '')} (선택지: {options})")
            return
        reader.traces.extend(self.codex_traces(name, raw, reader.cwd))

    @staticmethod
    def window(reader: Reader) -> tuple[datetime | None, datetime | None]:
        def parsed(value: str) -> datetime | None:
            try:
                return datetime.fromisoformat(value)
            except ValueError:
                return None

        return parsed(reader.started_at), parsed(reader.ended_at)


extractServiceDI = Annotated[ExtractService, Depends(ExtractService)]  # noqa: N816
