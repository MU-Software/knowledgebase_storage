from __future__ import annotations

from collections import Counter
from typing import Annotated

from fastapi import Depends

from backend.repositories.project import ProjectRepository, projectRepositoryDI
from backend.services import ServiceImpl

PATH_SHARE = 0.75
PATH_MIN_VOTES = 3
SHORT_REPLY_CHARS = 40
EDIT_WEIGHT = 3
ACTIVE_WEIGHT = 2


class SplitService(ServiceImpl[ProjectRepository]):
    repository: projectRepositoryDI

    @staticmethod
    def votes_of(segment: str, prefixes: dict[str, str]) -> Counter[str]:
        votes: Counter[str] = Counter()
        for line in segment.splitlines():
            weight = EDIT_WEIGHT if line.startswith("도구:") and "edit " in line else ACTIVE_WEIGHT if line.startswith("(활성 파일") else 1
            for name, prefix in prefixes.items():
                if prefix and prefix in line:
                    votes[name] += weight * line.count(prefix)
        return votes

    def decided(self, segment: str, prefixes: dict[str, str], previous: str) -> tuple[str, str, dict[str, int]]:
        votes = self.votes_of(segment, prefixes)
        total = sum(votes.values())
        if total >= PATH_MIN_VOTES:
            top, count = votes.most_common(1)[0]
            if count / total >= PATH_SHARE:
                return top, "path", dict(votes)
        request = segment.split("\n", 2)[1] if "\n" in segment else segment
        if total == 0 and len(request.strip()) < SHORT_REPLY_CHARS:
            return previous, "continue", {}
        return "", "llm", dict(votes)

    def plan(self, segments: list[str], prefixes: dict[str, str], container: str) -> list[dict[str, object]]:
        rows: list[dict[str, object]] = []
        previous = container
        for number, segment in enumerate(segments, 1):
            project, how, votes = self.decided(segment, prefixes, previous)
            rows.append({"number": number, "project": project or container, "how": how, "votes": votes})
            if project:
                previous = project
        return rows


splitServiceDI = Annotated[SplitService, Depends(SplitService)]  # noqa: N816
