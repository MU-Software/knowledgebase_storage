from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from anthropic import AsyncAnthropic

from backend.summarizers.base import Summarizer

if TYPE_CHECKING:
    from backend.schemas import LLMProviderResolved

TOOL_NAME = "emit_note"


class AnthropicSummarizer(Summarizer):
    def __init__(self, provider: LLMProviderResolved, language: str) -> None:
        self.client = AsyncAnthropic(api_key=provider.api_key, timeout=provider.timeout_seconds)
        self.model = provider.model
        self.context_tokens = provider.context_tokens
        self.language = language

    async def complete(self, system: str, user: str, *, schema: dict[str, Any] | None = None) -> str:
        structured_kwargs: dict[str, Any] = (
            {}
            if schema is None
            else {
                "tools": [{"name": TOOL_NAME, "description": "Return what you assembled.", "input_schema": schema}],
                "tool_choice": {"type": "tool", "name": TOOL_NAME},
            }
        )
        response = await self.client.messages.create(
            model=self.model,
            max_tokens=8192,
            system=system,
            messages=[{"role": "user", "content": user}],
            **structured_kwargs,
        )
        for block in response.content:
            if schema is not None and block.type == "tool_use":
                return json.dumps(block.input, ensure_ascii=False)
            if schema is None and block.type == "text":
                return block.text
        message = "no expected block in the Anthropic response"
        raise RuntimeError(message)
