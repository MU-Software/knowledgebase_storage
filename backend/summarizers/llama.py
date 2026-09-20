from __future__ import annotations

from typing import TYPE_CHECKING, Any

from httpx import AsyncClient, Timeout

from backend.summarizers.base import Summarizer

if TYPE_CHECKING:
    from backend.schemas import LLMProviderResolved


class LlamaCppSummarizer(Summarizer):
    def __init__(self, provider: LLMProviderResolved, language: str) -> None:
        headers = {"Content-Type": "application/json"}
        if provider.api_key:
            headers["Authorization"] = f"Bearer {provider.api_key}"
        self.client = AsyncClient(
            base_url=provider.base_url or "",
            headers=headers,
            timeout=Timeout(provider.timeout_seconds, connect=provider.connect_timeout_seconds),
        )
        self.model = provider.model
        self.context_tokens = provider.context_tokens
        self.language = language

    async def complete(self, system: str, user: str, *, schema: dict[str, Any] | None = None) -> str:
        payload: dict[str, object] = {
            "model": self.model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "temperature": 0.2,
            # summarizing is extraction, not deduction; non-reasoning templates ignore this
            "chat_template_kwargs": {"enable_thinking": False},
        }
        if schema is not None:
            payload["response_format"] = {"type": "json_schema", "json_schema": {"name": "result", "schema": schema}}

        response = await self.client.post("/chat/completions", json=payload)
        response.raise_for_status()
        content = response.json()["choices"][0]["message"].get("content") or ""
        if schema is not None:
            start, end = content.find("{"), content.rfind("}")
            if start == -1 or end == -1:
                message = f"no JSON object found in response: {content[:200]}"
                raise ValueError(message)
            content = content[start : end + 1]
        return content
