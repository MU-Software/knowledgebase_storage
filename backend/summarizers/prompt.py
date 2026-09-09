from __future__ import annotations

from typing import Any

from backend.schemas import SummaryResult

SYSTEM_PROMPT_TEMPLATE = """\
You are a librarian who turns development session logs into knowledge-base notes.

Rules:
- Do not transcribe. Keep only what is worth reading again later.
- Drop tool-call logs, file listings and progress chatter.
- Never lose decisions, unresolved problems and next steps.
- Write in {language}. Leave code identifiers and commands verbatim.
- relations.target is the title of another note. Leave it empty when unsure.
"""

MAP_INSTRUCTION = """\
Below is one part of a session. Summarize only what is worth keeping from this part.
Do not guess the conclusion of the whole session; record only what this fragment actually contains.
"""

REDUCE_INSTRUCTION = """\
Below are per-part summaries of a single session. Merge them into one note.
Remove duplicates; when parts contradict each other, prefer the later one.
"""

SINGLE_INSTRUCTION = """\
Summarize the session below into a single note.
"""

RESULT_JSON_SCHEMA = SummaryResult.model_json_schema()

_CHARS_PER_TOKEN = 2.0


def system_prompt(language: str) -> str:
    return SYSTEM_PROMPT_TEMPLATE.format(language=language)


def estimate_tokens(text: str) -> int:
    return int(len(text) / _CHARS_PER_TOKEN) + 1


def render_transcript(messages: list[dict[str, Any]]) -> str:
    lines = []
    for message in messages:
        role = str(message.get("role", "unknown"))
        content = message.get("content", "")
        if isinstance(content, list):
            content = "\n".join(str(block.get("text", "")) for block in content if isinstance(block, dict))
        lines.append(f"### {role}\n{content}".strip())
    return "\n\n".join(lines)


def split_text(text: str, budget_tokens: int) -> list[str]:
    if estimate_tokens(text) <= budget_tokens:
        return [text]
    size = max(int((budget_tokens - 1) * _CHARS_PER_TOKEN), 1)
    return [text[start : start + size] for start in range(0, len(text), size)]


def chunk_transcript(messages: list[dict[str, Any]], budget_tokens: int) -> list[str]:
    chunks: list[str] = []
    current: list[dict[str, Any]] = []
    current_tokens = 0

    def flush() -> None:
        nonlocal current, current_tokens
        if current:
            chunks.extend(split_text(render_transcript(current), budget_tokens))
            current, current_tokens = [], 0

    for message in messages:
        tokens = estimate_tokens(render_transcript([message]))
        if tokens > budget_tokens:
            flush()
            chunks.extend(split_text(render_transcript([message]), budget_tokens))
            continue
        if current and current_tokens + tokens > budget_tokens:
            flush()
        current.append(message)
        current_tokens += tokens

    flush()
    return chunks


def build_user_prompt(instruction: str, body: str, context: str) -> str:
    return f"{instruction}\n{context}\n\n---\n\n{body}"


def job_context(agent: str, project: str, device: str) -> str:
    return f"project: {project} / agent: {agent} / device: {device}"
