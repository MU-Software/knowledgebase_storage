from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from backend.schemas import SessionChunk, SessionHeader, SessionItem, SessionNote, SplitChoice, VerifyVerdict

if TYPE_CHECKING:
    from backend.summarizers.base import Summarizer, Toolbox

logger = logging.getLogger(__name__)

CHUNK_CHARS = 13000
EARLIER_ITEMS = 8
VERIFIED_CATEGORIES = frozenset({"change", "fact"})
WRONG = frozenset({"contradicted"})
MAX_VERIFIED = 12
EXCERPT_CHARS = 3500
MIN_CORRECTION_CHARS = 15


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


class SessionPipeline:
    def __init__(self, summarizer: Summarizer, prompts: dict[str, dict[str, Any]], tools: Toolbox | None, verified: int = MAX_VERIFIED) -> None:
        self.summarizer = summarizer
        self.prompts = prompts
        self.tools = tools
        self.verified_at_most = verified

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
            lines = "\n".join(f"- {item.request} → {item.outcome}" for item in earlier[-EARLIER_ITEMS:])
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

    async def run(self, segments: list[str], background: str) -> SessionNote:
        found: dict[int, SessionItem] = {}
        groups = grouped(segments, CHUNK_CHARS)
        logger.info("summarizing %d request(s) in %d chunk(s)", len(segments), len(groups))
        for position, group in enumerate(groups, 1):
            logger.info("chunk %d/%d covers requests %s", position, len(groups), group)
            chunk = "\n\n".join(segments[number - 1] for number in group)
            earlier = [found[number] for number in sorted(found)]
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

        items = [found[number] for number in sorted(found)]
        logger.info("verifying the claims of %d request(s)", len(items))
        corrected = await self.verified(items, background)
        logger.info("verification corrected %d claim(s); writing the header", corrected)
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
        return await self.summarizer.shaped(system, "\n".join(lines), SessionHeader, settings)
