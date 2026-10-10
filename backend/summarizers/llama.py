from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from httpx import AsyncClient, HTTPStatusError, ReadTimeout, Timeout, TransportError, codes

from backend.summarizers.base import ANSWER_TOKENS, ROUND_ANSWER_TOKENS, ROUND_TOKENS, TOOL_FAILED, R, Summarizer, Toolbox, strict
from backend.summarizers.prompt import CHARS_PER_TOKEN, estimate_tokens

if TYPE_CHECKING:
    from backend.schemas import LLMProviderResolved


logger = logging.getLogger(__name__)

REPEATED = "already shown above; do not repeat"
REPEATED_CLIPPED = "shown above but cut to fit the context; ask for a narrower path or line range instead"
UNREADABLE = "the arguments were not a JSON object; call the tool again with valid JSON arguments"
CLIPPED = "\n… cut to fit the context; ask for a narrower path or line range to see the rest"
RETRY_SECONDS = (5.0, 20.0)
SHRINKS = 2
MIN_TOOL_TOKENS = 2000
ROUND_SHARE = 0.5
MIN_RESULT_CHARS = 1000
ERROR_BODY_CHARS = 500


def used_tokens(messages: list[dict[str, Any]]) -> int:
    return sum(estimate_tokens(f"{message.get('content') or ''}{json.dumps(message.get('tool_calls') or [])}") for message in messages)


@dataclass
class Lookups:
    tools: Toolbox
    budget: int
    seen: dict[str, dict[str, Any]] = field(default_factory=dict)

    async def answer(self, calls: list[dict[str, Any]], parsed: list[dict[str, Any] | None], messages: list[dict[str, Any]]) -> None:
        answers: list[tuple[dict[str, Any], str, str, str]] = []
        for call, arguments in zip(calls, parsed, strict=True):
            name = call["function"]["name"]
            key = name + json.dumps(arguments, sort_keys=True)
            if arguments is None:
                answers.append((call, UNREADABLE, key, "note"))
            elif key in self.seen:
                answers.append((call, REPEATED_CLIPPED if self.seen[key]["content"].endswith(CLIPPED) else REPEATED, key, "note"))
            elif any(held == key for _, _, held, kind in answers if kind == "result"):
                answers.append((call, REPEATED_CLIPPED, key, "twin"))
            else:
                result = await self.tools.run(name, arguments)
                logger.info("%s(%s) returned %d chars", name, json.dumps(arguments, ensure_ascii=False)[:120], len(result))
                answers.append((call, result, key, "result"))

        results = [(text, key) for _, text, key, kind in answers if kind == "result"]
        notes = sum(len(text) for _, text, _, kind in answers if kind != "result")
        left = max(int((self.budget - used_tokens(messages) - MIN_TOOL_TOKENS) * CHARS_PER_TOKEN * ROUND_SHARE) - notes, 0)
        shown: dict[str, str] = {}
        for position, (text, key) in enumerate(sorted(results, key=lambda result: len(result[0]))):
            share = left // (len(results) - position)
            shown[key] = text if len(text) <= share else text[:share] + CLIPPED
            left -= min(len(text), share)

        for call, text, key, kind in answers:
            content = text
            if kind == "result" or (kind == "twin" and shown[key].startswith(TOOL_FAILED)):
                content = shown[key]
            elif kind == "twin":
                content = REPEATED_CLIPPED if shown[key].endswith(CLIPPED) else REPEATED
            message = {"role": "tool", "tool_call_id": call.get("id", ""), "content": content}
            messages.append(message)
            if kind == "result" and not text.startswith(TOOL_FAILED):
                self.seen[key] = message


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
        self.retry_seconds = RETRY_SECONDS

    async def post(self, payload: dict[str, Any]) -> dict[str, Any]:
        for wait in (*self.retry_seconds, None):
            try:
                response = await self.client.post("/chat/completions", json=payload)
            except TransportError as exc:
                if wait is None or isinstance(exc, ReadTimeout):
                    raise
                logger.warning("the server dropped the request (%s); trying again in %.0fs", exc, wait)
            else:
                if response.status_code < codes.INTERNAL_SERVER_ERROR or wait is None:
                    break
                logger.warning("the server answered %d (%s); trying again in %.0fs", response.status_code, response.text[:200], wait)
            await asyncio.sleep(wait)
        if response.is_error:
            message = f"{response.status_code} from {response.request.url}: {response.text[:ERROR_BODY_CHARS]}"
            raise HTTPStatusError(message, request=response.request, response=response)
        return response.json()

    async def answered(self, payload: dict[str, Any], messages: list[dict[str, Any]]) -> dict[str, Any]:
        for _ in range(SHRINKS):
            try:
                return await self.post(payload)
            except HTTPStatusError as exc:
                long = [message for message in messages if message["role"] == "tool" and len(message["content"]) > MIN_RESULT_CHARS]
                if exc.response.status_code != codes.BAD_REQUEST or "context" not in exc.response.text.lower() or not long:
                    raise
                logger.warning("the server ran out of context; halving the longer tool results")
                for message in long:
                    message["content"] = message["content"][: len(message["content"]) // 2] + CLIPPED
        return await self.post(payload)

    async def rounds(self, system: str, user: str, tools: Toolbox, schema: dict[str, Any] | None, settings: dict[str, Any] | None) -> str:
        chosen = settings or {}
        max_tokens = chosen.get("max_tokens", ROUND_TOKENS)
        answer = min(max_tokens, ROUND_ANSWER_TOKENS)
        budget = self.budget - answer - estimate_tokens(json.dumps(tools.specs))
        messages: list[dict[str, Any]] = [{"role": "system", "content": system}, {"role": "user", "content": user}]
        lookups = Lookups(tools, budget)
        for round_number in range(tools.rounds + 1):
            roomy = budget - used_tokens(messages) >= MIN_TOOL_TOKENS
            if round_number == 0 and not roomy:
                logger.warning("the prompt leaves no room for tools, so nothing is looked up")
                return ""
            last = round_number == tools.rounds or not roomy
            if last:
                messages.append({"role": "user", "content": "No more tools. Answer now from what you found."})
            payload: dict[str, Any] = {
                "model": self.model,
                "messages": messages,
                "temperature": chosen.get("temperature", 0.2),
                "max_tokens": max_tokens,
                "chat_template_kwargs": {"enable_thinking": bool(chosen.get("thinking", False))},
            }
            if last and schema is not None:
                payload["response_format"] = {"type": "json_schema", "json_schema": {"name": "result", "schema": schema}}
            elif not last:
                payload["tools"] = tools.specs
            message = (await self.answered(payload, messages))["choices"][0]["message"]
            calls = message.get("tool_calls") or []
            parsed: list[dict[str, Any] | None] = []
            for call in calls:
                raw = call["function"].get("arguments")
                try:
                    arguments = raw if isinstance(raw, dict) else json.loads(raw or "{}")
                except (TypeError, json.JSONDecodeError):
                    arguments = None
                parsed.append(arguments if isinstance(arguments, dict) else None)
                call["function"]["arguments"] = json.dumps(parsed[-1] or {}, ensure_ascii=False)
            messages.append({key: value for key, value in message.items() if key in ("role", "content", "tool_calls")})
            logger.info("round %d/%d answered with %d tool call(s)", round_number + 1, tools.rounds + 1, len(calls))
            if not calls or last:
                return str(message.get("content") or "").strip()
            await lookups.answer(calls, parsed, messages)
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
            "max_tokens": chosen.get("max_tokens", ANSWER_TOKENS),
            "chat_template_kwargs": {"enable_thinking": bool(chosen.get("thinking", False))},
        }
        if schema is not None:
            payload["response_format"] = {"type": "json_schema", "json_schema": {"name": "result", "schema": schema}}

        choice = (await self.post(payload))["choices"][0]
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
