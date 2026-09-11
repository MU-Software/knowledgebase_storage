from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from backend.models import MIN_BUDGET_TOKENS, MIN_CONTEXT_TOKENS, PROMPT_OVERHEAD_TOKENS
from backend.schemas import SummaryResult
from backend.summarizers import prompt

MAX_REDUCE_ROUNDS = 5


class Summarizer(ABC):
    context_tokens: int
    language: str

    @abstractmethod
    async def complete(self, system: str, user: str, *, structured: bool) -> str: ...

    async def summarize(self, messages: list[dict[str, Any]], *, agent: str, project: str, device: str) -> SummaryResult:
        system = prompt.system_prompt(self.language)
        context = prompt.job_context(agent=agent, project=project, device=device)
        budget = self.context_tokens - PROMPT_OVERHEAD_TOKENS
        if budget < MIN_BUDGET_TOKENS:
            msg = f"context_tokens {self.context_tokens} leaves no room for the prompt; needs at least {MIN_CONTEXT_TOKENS}"
            raise ValueError(msg)
        chunks = prompt.chunk_transcript(messages, budget)

        instruction = prompt.SINGLE_INSTRUCTION
        body = chunks[0] if chunks else ""

        for _ in range(MAX_REDUCE_ROUNDS):
            if len(chunks) <= 1:
                break
            partials = [
                await self.complete(system, prompt.build_user_prompt(prompt.MAP_INSTRUCTION, chunk, context), structured=False) for chunk in chunks
            ]
            instruction = prompt.REDUCE_INSTRUCTION
            body = "\n\n---\n\n".join(partials)
            chunks = prompt.split_text(body, budget)
        else:
            if len(chunks) > 1:
                msg = f"summary did not fit the context budget after repeated reduction ({len(chunks)} chunks left)"
                raise RuntimeError(msg)

        body = chunks[0] if chunks else body
        user = prompt.build_user_prompt(instruction, body, context)
        return SummaryResult.model_validate_json(await self.complete(system, user, structured=True))
