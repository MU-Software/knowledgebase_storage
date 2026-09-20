from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from backend.models import MIN_BUDGET_TOKENS, MIN_CONTEXT_TOKENS, PROMPT_OVERHEAD_TOKENS
from backend.schemas import NoteRelations, ProjectSuggestions, SummaryResult
from backend.summarizers import prompt

MAX_REDUCE_ROUNDS = 5


class Summarizer(ABC):
    context_tokens: int
    language: str

    @abstractmethod
    async def complete(self, system: str, user: str, *, schema: dict[str, Any] | None = None) -> str: ...

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

    async def summarize_project(self, records: list[dict[str, Any]], *, project: str, children: list[str]) -> SummaryResult:
        return await self.fold(records, prompt.overview_recipe(self.language, project=project, children=children))

    async def merge_memories(self, documents: list[dict[str, Any]], *, project: str, name: str) -> str:
        system = prompt.memory_merge_system_prompt(self.language)
        user = prompt.build_user_prompt(prompt.MEMORY_MERGE_INSTRUCTION, prompt.render_documents(documents), f"project: {project} / file: {name}")
        if prompt.estimate_tokens(user) * 2 > self.budget:
            msg = f"{name} and its counterpart do not fit the context budget of this provider"
            raise ValueError(msg)
        return (await self.complete(system, user)).strip() + "\n"

    async def suggest_projects(self, catalog: list[dict[str, Any]]) -> ProjectSuggestions:
        body = prompt.first_that_fits(prompt.render_documents(catalog), self.budget)
        user = prompt.build_user_prompt(prompt.SUGGESTION_INSTRUCTION, body, "")
        answer = await self.complete(prompt.suggestion_system_prompt(self.language), user, schema=prompt.SUGGESTIONS_JSON_SCHEMA)
        return ProjectSuggestions.model_validate_json(answer)

    async def find_relations(self, note: list[dict[str, Any]], *, candidates: list[str]) -> NoteRelations:
        body = prompt.first_that_fits(prompt.render_documents(note), self.budget // 2)
        titles = prompt.first_that_fits("\n".join(f"- {title}" for title in candidates), self.budget // 2)
        user = prompt.build_user_prompt(prompt.RELATION_INSTRUCTION, f"{body}\n\n---\n\n{titles}", "")
        answer = await self.complete(prompt.relation_system_prompt(self.language), user, schema=prompt.RELATIONS_JSON_SCHEMA)
        return NoteRelations.model_validate_json(answer)
