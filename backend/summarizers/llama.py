from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any

from httpx import AsyncClient, Timeout

from backend.summarizers.base import R, Summarizer, Toolbox, strict

if TYPE_CHECKING:
    from backend.schemas import LLMProviderResolved


logger = logging.getLogger(__name__)

REPEATED = "already shown above; do not repeat"


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

    async def rounds(self, system: str, user: str, tools: Toolbox, schema: dict[str, Any] | None, settings: dict[str, Any] | None) -> str:
        chosen = settings or {}
        messages: list[dict[str, Any]] = [{"role": "system", "content": system}, {"role": "user", "content": user}]
        seen: set[str] = set()
        for round_number in range(tools.rounds + 1):
            last = round_number == tools.rounds
            if last:
                messages.append({"role": "user", "content": "No more tools. Answer now from what you found."})
            payload: dict[str, Any] = {
                "model": self.model,
                "messages": messages,
                "temperature": chosen.get("temperature", 0.2),
                "max_tokens": chosen.get("max_tokens", 8192),
                "chat_template_kwargs": {"enable_thinking": bool(chosen.get("thinking", False))},
            }
            if last and schema is not None:
                payload["response_format"] = {"type": "json_schema", "json_schema": {"name": "result", "schema": schema}}
            elif not last:
                payload["tools"] = tools.specs
            response = await self.client.post("/chat/completions", json=payload)
            response.raise_for_status()
            message = response.json()["choices"][0]["message"]
            messages.append({key: value for key, value in message.items() if key in ("role", "content", "tool_calls")})
            calls = message.get("tool_calls") or []
            logger.info("round %d/%d answered with %d tool call(s)", round_number + 1, tools.rounds + 1, len(calls))
            if not calls:
                return str(message.get("content") or "").strip()
            for call in calls:
                name = call["function"]["name"]
                try:
                    arguments = json.loads(call["function"]["arguments"] or "{}")
                except json.JSONDecodeError:
                    arguments = {}
                key = name + json.dumps(arguments, sort_keys=True)
                result = REPEATED if key in seen else await tools.run(name, arguments)
                logger.info("%s(%s) returned %d chars", name, json.dumps(arguments, ensure_ascii=False)[:120], len(result))
                seen.add(key)
                messages.append({"role": "tool", "tool_call_id": call.get("id", ""), "content": result})
        return ""

    async def explore(self, system: str, user: str, tools: Toolbox, settings: dict[str, Any] | None = None) -> str:
        return await self.rounds(system, user, tools, None, settings)

    async def investigate(self, system: str, user: str, tools: Toolbox, model: type[R], settings: dict[str, Any] | None = None) -> R | None:
        answer = await self.rounds(system, user, tools, strict(model.model_json_schema()), settings)
        start, end = answer.find("{"), answer.rfind("}")
        if start == -1 or end == -1:
            return None
        try:
            return model.model_validate_json(answer[start : end + 1])
        except ValueError:
            return None

    async def complete(self, system: str, user: str, *, schema: dict[str, Any] | None = None, settings: dict[str, Any] | None = None) -> str:
        chosen = settings or {}
        payload: dict[str, object] = {
            "model": self.model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "temperature": chosen.get("temperature", 0.2),
            "max_tokens": chosen.get("max_tokens", 20000),
            "chat_template_kwargs": {"enable_thinking": bool(chosen.get("thinking", False))},
        }
        if schema is not None:
            payload["response_format"] = {"type": "json_schema", "json_schema": {"name": "result", "schema": schema}}

        response = await self.client.post("/chat/completions", json=payload)
        response.raise_for_status()
        choice = response.json()["choices"][0]
        if choice.get("finish_reason") == "length" and chosen.get("thinking"):
            return await self.complete(system, user, schema=schema, settings={**chosen, "thinking": False})
        content = choice["message"].get("content") or ""
        if schema is not None:
            start, end = content.find("{"), content.rfind("}")
            if start == -1 or end == -1:
                message = f"no JSON object found in response: {content[:200]}"
                raise ValueError(message)
            content = content[start : end + 1]
        return content
