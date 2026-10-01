from __future__ import annotations

import re

PROJECTS_ROOT = "projects"
LOG_DIR = "log"
MEMORY_DIR = "memory"
ARCHIVE_DIR = "archive"
RESERVED_SEGMENTS = frozenset({LOG_DIR, MEMORY_DIR, ARCHIVE_DIR})
OVERVIEW_FILE = "overview.md"
UNFILED = "_unfiled"

_UNSAFE = re.compile(r"[^\w.-]+", re.UNICODE)
_FILENAME = re.compile(r"\.(pdf|md|txt|png|jpe?g|gif|svg|csv|xlsx?|docx?|zip|html?|json|ya?ml)$", re.IGNORECASE)


def slugify(value: str) -> str:
    slug = _UNSAFE.sub("-", value.strip()).strip("-").lower() or "unknown"
    return f"{slug}-project" if slug in RESERVED_SEGMENTS else slug


def named_project(value: str) -> str:
    name = value.strip()
    if not name or "://" in name or name.startswith(("http", "/", "~", ".")) or "/" in name or "\\" in name:
        return UNFILED
    return UNFILED if _FILENAME.search(name) else name


def project_of(relative_path: str) -> str:
    parts = relative_path.split("/")
    if parts[0] != PROJECTS_ROOT or len(parts) < 3 or parts[1] in RESERVED_SEGMENTS:  # noqa: PLR2004
        return UNFILED
    return parts[1]
