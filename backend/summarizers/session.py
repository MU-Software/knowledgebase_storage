from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Protocol

from pydantic import ValidationError

from backend.schemas import SessionChunk, SessionHeader, SessionItem, SessionNote, SplitChoice, VerifyVerdict
from backend.summarizers.base import ANSWER_TOKENS, ROUND_ANSWER_TOKENS, ROUND_TOKENS
from backend.summarizers.prompt import CHARS_PER_TOKEN, estimate_tokens

if TYPE_CHECKING:
    from backend.summarizers.base import Summarizer, Toolbox

logger = logging.getLogger(__name__)


class Report(Protocol):
    async def __call__(self, progress: str, *, done: bool = False) -> None: ...


CHUNK_CHARS = 13000
SEGMENT_HEAD = 2 / 5
EARLIER_LINE_CHARS = 200
MIN_HEADER_LINE_CHARS = 80
EARLIER_TOKENS = 1000
CHUNK_ANSWER_TOKENS = 8192
EARLIER_ITEMS = 8
VERIFIED_CATEGORIES = frozenset({"change", "fact"})
WRONG = frozenset({"contradicted"})
MAX_VERIFIED = 12
EXCERPT_CHARS = 3500
MIN_CORRECTION_CHARS = 15
RESUMABLE_STAGES = ("session_explore", "session_chunk")


def grouped(rendered: list[str], budget: int) -> list[list[int]]:
    groups: list[list[int]] = [[]]
    for number, text in enumerate(rendered, 1):
        if groups[-1] and sum(len(rendered[held - 1]) for held in groups[-1]) + len(text) > budget:
            groups.append([])
        groups[-1].append(number)
    return groups


def quoted_in(quote: str, text: str) -> bool:
    flat = " ".join(text.split())
    needle = " ".join(quote.split())
    return len(needle) > 10 and needle[:120] in flat  # noqa: PLR2004


@dataclass
class SessionCheckpoint:
    digest: str
    segments: dict[int, str]
    found: dict[int, SessionItem] = field(default_factory=dict)
    makers: dict[int, str] = field(default_factory=dict)

    @classmethod
    def resumed(cls, saved: dict[str, Any] | None, segments: list[str], prompts: dict[str, Any]) -> SessionCheckpoint:
        stages = "\x00".join(json.dumps(prompts.get(stage), sort_keys=True) for stage in RESUMABLE_STAGES)
        hashes = {number: hashlib.sha256(segment.encode()).hexdigest() for number, segment in enumerate(segments, 1)}
        checkpoint = cls(hashlib.sha256(stages.encode()).hexdigest(), hashes)
        saved = saved or {}
        if saved.get("digest") != checkpoint.digest:
            return checkpoint
        try:
            items = [SessionItem.model_validate(raw) for raw in saved.get("items") or []]
        except ValidationError as exc:
            logger.warning("ignoring a checkpoint that no longer reads: %s", exc)
            return checkpoint
        stored, makers = saved.get("segments") or {}, saved.get("makers") or {}
        for item in items:
            if item.number in hashes and stored.get(str(item.number)) == hashes[item.number]:
                checkpoint.found[item.number] = item
                if maker := makers.get(str(item.number)):
                    checkpoint.makers[item.number] = str(maker)
        return checkpoint

    def credit(self, provider: str) -> None:
        for number in self.found.keys() - self.makers.keys():
            self.makers[number] = provider

    def credited(self, provider: str, priorities: dict[str, int]) -> str:
        named = {name for name in self.makers.values() if name in priorities} | {provider}
        return max(named, key=lambda name: (priorities.get(name, 0), name))

    def saved(self) -> dict[str, Any]:
        return {
            "digest": self.digest,
            "items": [item.model_dump(mode="json") for item in self.found.values()],
            "segments": {str(number): self.segments.get(number) for number in self.found},
            "makers": {str(number): maker for number, maker in self.makers.items() if number in self.found},
        }


class SessionPipeline:
    def __init__(self, summarizer: Summarizer, prompts: dict[str, dict[str, Any]], tools: Toolbox | None, verified: int = MAX_VERIFIED) -> None:
        self.summarizer = summarizer
        self.prompts = prompts
        self.tools = tools
        self.verified_at_most = verified

    def room(self, reserved: int, *texts: str) -> int:
        return int((self.summarizer.budget - reserved - sum(estimate_tokens(text) for text in texts)) * CHARS_PER_TOKEN)

    def stage(self, name: str) -> tuple[str, dict[str, Any]]:
        prompt = self.prompts.get(name) or {}
        return str(prompt.get("system", "")), prompt

    async def explored(self, chunk: str, background: str) -> str:
        system, settings = self.stage("session_explore")
        if self.tools is None or not system:
            return ""
        return await self.summarizer.explore(system, f"{background}Session log part:\n\n{chunk}", self.tools, settings)

    async def chunk_items(self, chunk: str, background: str, earlier: list[SessionItem]) -> list[SessionItem]:
        system, settings = self.stage("session_chunk")
        context = background
        logger.info("exploring the repository for a chunk of %d chars", len(chunk))
        if found := await self.explored(chunk, background):
            context += f"What the repository shows, checked before writing:\n{found}\n\n---\n\n"
        if earlier:
            lines = "\n".join(f"- {item.request} → {item.outcome}"[:EARLIER_LINE_CHARS] for item in earlier[-EARLIER_ITEMS:])
            context += f"Earlier in this session:\n{lines}\n\n---\n\n"
        result = await self.summarizer.shaped(system, f"{context}Session log:\n\n{chunk}", SessionChunk, settings)
        for item in result.items:
            item.links = [link for link in item.links if quoted_in(link.quote, chunk)]
        return result.items

    @staticmethod
    def rewritten(original: str, correction: str) -> str:
        wanted = correction.strip()
        for arrow in ("->", "→"):
            if arrow in wanted:
                wanted = wanted.rsplit(arrow, 1)[1].strip()
        return wanted if len(wanted) > MIN_CORRECTION_CHARS and wanted != original.strip() else ""

    async def verified(self, items: list[SessionItem], background: str) -> int:
        system, settings = self.stage("session_verify")
        if self.tools is None or not system:
            return 0
        corrected = 0
        claims = [observation for item in items for observation in item.observations if observation.category in VERIFIED_CATEGORIES]
        logger.info("%d claim(s) can be checked against the repository, checking %d", len(claims), min(len(claims), self.verified_at_most))
        for observation in claims[: self.verified_at_most]:
            verdict = await self.summarizer.investigate(system, f"{background}Claim: {observation.text}", self.tools, VerifyVerdict, settings)
            if verdict is None:
                continue
            if verdict.verdict not in WRONG or not verdict.evidence.strip():
                continue
            if wanted := self.rewritten(observation.text, verdict.correction):
                logger.info("the repository contradicts a claim, rewriting it: %s", verdict.evidence[:200])
                observation.text = wanted
                corrected += 1
        return corrected

    async def filed(
        self,
        segments: list[str],
        split: list[dict[str, Any]],
        candidates: list[str],
        names: list[str],
        container: str,
    ) -> dict[int, str]:
        system, settings = self.stage("session_split")
        if not split or not system:
            return {}
        known = set(names)
        decided: dict[int, str] = {}
        previous = container
        for row in split:
            number = int(row["number"])
            if row["how"] != "llm":
                decided[number] = str(row["project"])
                previous = decided[number]
                continue
            listed = "\n".join(candidates)
            seen = f"Projects:\n{listed}\n\nPrevious request was filed under: {previous}\n\n---\n\n{segments[number - 1][:EXCERPT_CHARS]}"
            choice = await self.summarizer.shaped(system, seen, SplitChoice, settings)
            decided[number] = choice.project if choice.project in known else previous
            previous = decided[number]
        return decided

    async def run(self, segments: list[str], background: str, found: dict[int, SessionItem], report: Report) -> SessionNote:
        chunk_system, chunk_settings = self.stage("session_chunk")
        explore_system, explore_settings = self.stage("session_explore")
        exploring = self.tools is not None and bool(explore_system)
        explored = min(int(explore_settings.get("max_tokens", ROUND_TOKENS)), ROUND_ANSWER_TOKENS) if exploring else 0
        answers = min(int(chunk_settings.get("max_tokens", ANSWER_TOKENS)), CHUNK_ANSWER_TOKENS) + explored
        limit = max(CHUNK_CHARS, self.room(answers + EARLIER_TOKENS, chunk_system, background))
        head = int(limit * SEGMENT_HEAD)
        segments = [
            f"{segment[:head]}\n\n… {len(segment) - limit} characters of this request left out …\n\n{segment[head + len(segment) - limit :]}"
            if len(segment) > limit
            else segment
            for segment in segments
        ]
        groups = grouped(segments, CHUNK_CHARS)
        logger.info("summarizing %d request(s) in %d chunk(s), %d already done", len(segments), len(groups), len(found))
        for position, group in enumerate(groups, 1):
            if all(number in found for number in group):
                continue
            logger.info("chunk %d/%d covers requests %s", position, len(groups), group)
            await report(f"chunk {position}/{len(groups)}")
            chunk = "\n\n".join(segments[number - 1] for number in group)
            earlier = [found[number] for number in sorted(found) if number < group[0]]
            for item in await self.chunk_items(chunk, background, earlier):
                if item.number in group and item.number not in found:
                    found[item.number] = item
            for number in group:
                if number in found:
                    continue
                alone = await self.chunk_items(segments[number - 1], background, [found[k] for k in sorted(found) if k < number])
                for item in alone:
                    if item.number == number:
                        found[number] = item
            await report(f"chunk {position}/{len(groups)} done", done=True)

        items = [found[number].model_copy(deep=True) for number in sorted(found)]
        logger.info("verifying the claims of %d request(s)", len(items))
        if self.tools is not None and self.stage("session_verify")[0]:
            await report("verifying claims")
        corrected = await self.verified(items, background)
        logger.info("verification corrected %d claim(s); writing the header", corrected)
        await report("writing the header")
        header = await self.header(items)
        self.prune(items, header)
        return SessionNote(header=header, items=items)

    @staticmethod
    def prune(items: list[SessionItem], header: SessionHeader) -> None:
        dropped = {pair.old for pair in header.superseded if 0 < pair.old < pair.by}
        header.superseded = []
        if not dropped:
            return
        counted = 0
        for item in items:
            kept = []
            for observation in item.observations:
                counted += 1
                if counted not in dropped:
                    kept.append(observation)
            item.observations = kept

    async def header(self, items: list[SessionItem]) -> SessionHeader:
        system, settings = self.stage("session_final")
        lines: list[str] = []
        counted = 0
        for item in items:
            lines.append(f"## {item.request}\n결과: {item.outcome}")
            for observation in item.observations:
                counted += 1
                lines.append(f"{counted}. [{observation.category}] {observation.text}")
        room = self.room(min(int(settings.get("max_tokens", ANSWER_TOKENS)), CHUNK_ANSWER_TOKENS), system)
        if lines and sum(len(line) + 1 for line in lines) > room:
            cap = max(room // len(lines), MIN_HEADER_LINE_CHARS)
            lines = [line[:cap] for line in lines]
        return await self.summarizer.shaped(system, "\n".join(lines), SessionHeader, settings)
