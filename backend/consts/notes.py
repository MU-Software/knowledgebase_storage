from __future__ import annotations

import re
from itertools import takewhile

PROJECTS_ROOT = "projects"
LOG_DIR = "log"
MEMORY_DIR = "memory"
ARCHIVE_DIR = "archive"
RESERVED_SEGMENTS = frozenset({LOG_DIR, MEMORY_DIR, ARCHIVE_DIR})
OVERVIEW_FILE = "overview.md"
UNFILED = "_unfiled"

_UNSAFE = re.compile(r"[^\w.-]+", re.UNICODE)


def slugify(value: str) -> str:
    slug = _UNSAFE.sub("-", value.strip()).strip("-").lower() or "unknown"
    return f"{slug}-project" if slug in RESERVED_SEGMENTS else slug


def project_segments(relative_path: str) -> list[str]:
    parts = relative_path.split("/")
    if parts[0] != PROJECTS_ROOT:
        return []
    return list(takewhile(lambda segment: segment not in RESERVED_SEGMENTS, parts[1:-1]))


def parent_of(project_path: str) -> str | None:
    head, _, _ = project_path.rpartition("/")
    return head or None


def ancestors_of(project_path: str) -> list[str]:
    found = []
    while (project_path := parent_of(project_path) or "") != "":
        found.append(project_path)
    return found


def is_within(project_path: str, ancestor: str) -> bool:
    return project_path == ancestor or project_path.startswith(f"{ancestor}/")
