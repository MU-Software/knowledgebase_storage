from __future__ import annotations

from typing import Any, NamedTuple

from backend.schemas import NoteRelations, ProjectOverview, ProjectSuggestions, SummaryResult

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

BACKGROUND_INSTRUCTION = """\
Notes from the projects this one sits under, as background. Lean on them to make sense of names and
decisions the session takes for granted, and do not summarize them: the note is about the session below.
"""

MEMORY_MERGE_SYSTEM_TEMPLATE = """\
You are a librarian who keeps one agent memory file for a project.

Rules:
- Keep every fact that still holds. Losing one is worse than keeping a duplicate.
- Fold entries that say the same thing into one, and keep the wording of the clearer entry.
- Keep the shape the documents share: the same headings, the same kind of lines.
- Each part opens with a line naming the project that copy came from. That line is not part of the file:
  never repeat it, and never group the result by it. One file comes out, not one section per project.
- Keep links such as [[other-memory]] and file paths verbatim.
- Write in {language}, and answer with the merged document only, with no preamble or code fence.
"""

MEMORY_MERGE_INSTRUCTION = """\
Below is the same memory file as two projects kept it, separated by ---.
Merge them into the one file the merged project will keep.
"""

OVERVIEW_SYSTEM_TEMPLATE = """\
You are a librarian who keeps one standing page per project.

Rules:
- Write what the project is and where it stands now, not a history of its sessions.
- Keep the decisions that still hold, the problems still open and the work still queued. Drop what has been superseded.
- When projects sit under this one, say what each of them is for and how they differ.
- Write in {language}. Leave code identifiers, commands and paths verbatim.
- relations.target is the title of another note. Point at the pages of the projects above and below this one.
"""

OVERVIEW_MAP_INSTRUCTION = """\
Below is part of one project's record. Keep only what still describes the project: what it is, what holds, what is open.
"""

OVERVIEW_REDUCE_INSTRUCTION = """\
Below are partial readings of one project. Merge them into its standing page.
Remove duplicates; when two disagree, prefer the later one.
"""

OVERVIEW_SINGLE_INSTRUCTION = """\
Below is a project's session notes, newest first, and the standing pages of the projects under it.
Write this project's standing page.
"""

SUGGESTION_SYSTEM_TEMPLATE = """\
You are a librarian who notices when two projects in a knowledge base are really one.

Rules:
- Suggest merge only when two names are the same work: a rename, a remote that moved, a directory and its repository.
- Suggest nest only when one is plainly a part of the other, such as a service of a system or a package of a monorepo.
- Sharing a language, a framework, an author or a machine is not a relation. Say nothing about those.
- A project already filed under another is settled. Say nothing about that pair.
- Copy the names exactly as they are given, and never name a project twice in one suggestion.
- An empty list is the right answer most of the time. Say nothing rather than guess.
- Write reason in {language}, in one sentence.
"""

SUGGESTION_INSTRUCTION = """\
Below is every project in the knowledge base with what is known about it.
Name the pairs that should become one project, or one filed under the other.
"""

RELATION_SYSTEM_TEMPLATE = """\
You are a librarian who links a note to the notes that continue its thread.

Rules:
- Link only notes that carry the same thread of work: the same bug, the same feature, the same decision.
- Copy target from the candidate list exactly. Never invent a title and never link a note to itself.
- type is a short verb phrase such as relates_to, follows or supersedes.
- Five links is many and none is a fine answer. Say nothing rather than guess.
- Write in {language}.
"""

RELATION_INSTRUCTION = """\
Below is one note, then the titles of the notes it could be linked to.
Name the ones it actually continues.
"""

MAX_BACKGROUND_CHARS = 4000


def _require_every_field(schema: dict[str, Any]) -> dict[str, Any]:
    """Defaulted fields drop out of `required`, and a constrained decoder then skips them."""
    for definition in (schema, *schema.get("$defs", {}).values()):
        if "properties" in definition:
            definition["required"] = list(definition["properties"])
    return schema


RESULT_JSON_SCHEMA = _require_every_field(SummaryResult.model_json_schema())
OVERVIEW_JSON_SCHEMA = _require_every_field(ProjectOverview.model_json_schema())
SUGGESTIONS_JSON_SCHEMA = _require_every_field(ProjectSuggestions.model_json_schema())
RELATIONS_JSON_SCHEMA = _require_every_field(NoteRelations.model_json_schema())

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


def memory_merge_system_prompt(language: str) -> str:
    return MEMORY_MERGE_SYSTEM_TEMPLATE.format(language=language)


def overview_system_prompt(language: str) -> str:
    return OVERVIEW_SYSTEM_TEMPLATE.format(language=language)


def suggestion_system_prompt(language: str) -> str:
    return SUGGESTION_SYSTEM_TEMPLATE.format(language=language)


def relation_system_prompt(language: str) -> str:
    return RELATION_SYSTEM_TEMPLATE.format(language=language)


def overview_context(project: str, children: list[str]) -> str:
    held = f" / projects inside it: {', '.join(children)}" if children else ""
    return f"project: {project}{held}"


class Recipe(NamedTuple):
    system: str
    context: str
    single: str
    mapped: str
    reduced: str
    schema: dict[str, Any]


def session_recipe(language: str, *, agent: str, project: str, device: str, background: str) -> Recipe:
    return Recipe(
        system=system_prompt(language),
        context=job_context(agent=agent, project=project, device=device, background=background),
        single=SINGLE_INSTRUCTION,
        mapped=MAP_INSTRUCTION,
        reduced=REDUCE_INSTRUCTION,
        schema=RESULT_JSON_SCHEMA,
    )


def overview_recipe(language: str, *, project: str, children: list[str]) -> Recipe:
    return Recipe(
        system=overview_system_prompt(language),
        context=overview_context(project, children),
        single=OVERVIEW_SINGLE_INSTRUCTION,
        mapped=OVERVIEW_MAP_INSTRUCTION,
        reduced=OVERVIEW_REDUCE_INSTRUCTION,
        schema=OVERVIEW_JSON_SCHEMA,
    )


def first_that_fits(text: str, budget_tokens: int) -> str:
    return split_text(text, budget_tokens)[0]


def render_documents(documents: list[dict[str, Any]]) -> str:
    return "\n\n---\n\n".join(str(document.get("content", "")) for document in documents)


def job_context(agent: str, project: str, device: str, background: str = "") -> str:
    header = f"project: {project} / agent: {agent} / device: {device}"
    return header if not background else f"{header}\n\n{BACKGROUND_INSTRUCTION}\n{background[:MAX_BACKGROUND_CHARS]}"
