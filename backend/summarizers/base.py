from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, TypeVar

from pydantic import BaseModel

from backend.models import MIN_BUDGET_TOKENS, MIN_CONTEXT_TOKENS, PROMPT_OVERHEAD_TOKENS
from backend.schemas import LinkVerdict, NoteRelations, SummaryResult
from backend.summarizers import prompt

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable


MAX_REDUCE_ROUNDS = 5
R = TypeVar("R", bound=BaseModel)


@dataclass
class Toolbox:
    specs: list[dict[str, Any]]
    run: Callable[[str, dict[str, Any]], Awaitable[str]]
    rounds: int = 6


def strict(schema: dict[str, Any]) -> dict[str, Any]:
    for definition in (schema, *schema.get("$defs", {}).values()):
        if "properties" in definition:
            definition["required"] = list(definition["properties"])
    return schema


class Summarizer(ABC):
    context_tokens: int
    language: str

    @abstractmethod
    async def complete(self, system: str, user: str, *, schema: dict[str, Any] | None = None, settings: dict[str, Any] | None = None) -> str: ...

    async def explore(self, system: str, user: str, tools: Toolbox, settings: dict[str, Any] | None = None) -> str:
        del system, user, tools, settings
        return ""

    async def investigate(self, system: str, user: str, tools: Toolbox, model: type[R], settings: dict[str, Any] | None = None) -> R | None:
        del system, user, tools, model, settings
        return None

    async def speak(self, system: str, user: str, settings: dict[str, Any] | None = None) -> str:
        return await self.complete(system, user, settings=settings)

    async def shaped(self, system: str, user: str, model: type[R], settings: dict[str, Any] | None = None) -> R:
        answer = await self.complete(system, user, schema=strict(model.model_json_schema()), settings=settings)
        start, end = answer.find("{"), answer.rfind("}")
        return model.model_validate_json(answer if start == -1 else answer[start : end + 1])

    @property
    def budget(self) -> int:
        budget = self.context_tokens - PROMPT_OVERHEAD_TOKENS
        if budget < MIN_BUDGET_TOKENS:
            msg = f"context_tokens {self.context_tokens} leaves no room for the prompt; needs at least {MIN_CONTEXT_TOKENS}"
            raise ValueError(msg)
        return budget

    async def fold(self, messages: list[dict[str, Any]], recipe: prompt.Recipe) -> SummaryResult:
        budget = self.budget
        chunks = prompt.chunk_transcript(messages, budget)
        instruction = recipe.single
        body = chunks[0] if chunks else ""

        for _ in range(MAX_REDUCE_ROUNDS):
            if len(chunks) <= 1:
                break
            partials = [await self.complete(recipe.system, prompt.build_user_prompt(recipe.mapped, chunk, recipe.context)) for chunk in chunks]
            instruction = recipe.reduced
            body = "\n\n---\n\n".join(partials)
            chunks = prompt.split_text(body, budget)
        else:
            if len(chunks) > 1:
                msg = f"summary did not fit the context budget after repeated reduction ({len(chunks)} chunks left)"
                raise RuntimeError(msg)

        body = chunks[0] if chunks else body
        user = prompt.build_user_prompt(instruction, body, recipe.context)
        return SummaryResult.model_validate_json(await self.complete(recipe.system, user, schema=recipe.schema))

    async def summarize(self, messages: list[dict[str, Any]], *, agent: str, project: str, device: str, background: str = "") -> SummaryResult:
        return await self.fold(messages, prompt.session_recipe(self.language, agent=agent, project=project, device=device, background=background))

    async def summarize_project(self, records: list[dict[str, Any]], *, project: str) -> SummaryResult:
        return await self.fold(records, prompt.overview_recipe(self.language, project=project))

    async def merge_memories(self, documents: list[dict[str, Any]], *, project: str, name: str) -> str:
        system = prompt.memory_merge_system_prompt(self.language)
        user = prompt.build_user_prompt(prompt.MEMORY_MERGE_INSTRUCTION, prompt.render_documents(documents), f"project: {project} / file: {name}")
        if prompt.estimate_tokens(user) * 2 > self.budget:
            msg = f"{name} and its counterpart do not fit the context budget of this provider"
            raise ValueError(msg)
        return (await self.complete(system, user)).strip() + "\n"

    async def judge_pair(self, records: list[dict[str, Any]], system: str, settings: dict[str, Any] | None = None) -> LinkVerdict:
        body = prompt.first_that_fits(prompt.render_documents(records), self.budget)
        return await self.shaped(system, body, LinkVerdict, settings)

    async def find_relations(self, note: list[dict[str, Any]], *, candidates: list[str]) -> NoteRelations:
        body = prompt.first_that_fits(prompt.render_documents(note), self.budget // 2)
        titles = prompt.first_that_fits("\n".join(f"- {title}" for title in candidates), self.budget // 2)
        user = prompt.build_user_prompt(prompt.RELATION_INSTRUCTION, f"{body}\n\n---\n\n{titles}", "")
        answer = await self.complete(prompt.relation_system_prompt(self.language), user, schema=prompt.RELATIONS_JSON_SCHEMA)
        return NoteRelations.model_validate_json(answer)
