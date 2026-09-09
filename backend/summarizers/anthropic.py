from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from anthropic import AsyncAnthropic

from backend.summarizers.base import Summarizer
from backend.summarizers.prompt import RESULT_JSON_SCHEMA

if TYPE_CHECKING:
    from backend.schemas import LLMProviderResolved

TOOL_NAME = "emit_note"


class AnthropicSummarizer(Summarizer):
    def __init__(self, provider: LLMProviderResolved, language: str) -> None:
        self.client = AsyncAnthropic(api_key=provider.api_key, timeout=provider.timeout_seconds)
        self.model = provider.model
        self.context_tokens = provider.context_tokens
        self.language = language

    async def complete(self, system: str, user: str, *, structured: bool) -> str:
        structured_kwargs: dict[str, Any] = (
            {
                "tools": [{"name": TOOL_NAME, "description": "Return the note you assembled.", "input_schema": RESULT_JSON_SCHEMA}],
                "tool_choice": {"type": "tool", "name": TOOL_NAME},
            }
            if structured
            else {}
        )
        response = await self.client.messages.create(
            model=self.model,
            max_tokens=8192,
            system=system,
            messages=[{"role": "user", "content": user}],
            **structured_kwargs,
        )
        for block in response.content:
            if structured and block.type == "tool_use":
                return json.dumps(block.input, ensure_ascii=False)
            if not structured and block.type == "text":
                return block.text
        message = "no expected block in the Anthropic response"
        raise RuntimeError(message)
