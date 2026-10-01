from __future__ import annotations

import asyncio
import shutil
from typing import TYPE_CHECKING

import typer
from sqlalchemy import delete
from sqlmodel import col

from backend.consts.notes import ARCHIVE_DIR, LOG_DIR, MEMORY_DIR, OVERVIEW_FILE, PROJECTS_ROOT
from backend.models import GitNetwork, GitSource, Job, JobKind, NoteDecision, NotePin, ProjectAlias, ProjectEntry, ProjectLink, RawTranscript, Upload
from backend.settings import get_settings

if TYPE_CHECKING:
    from pathlib import Path

    from sqlmodel import SQLModel
    from sqlmodel.ext.asyncio.session import AsyncSession

DECIDED: tuple[tuple[str, type[SQLModel]], ...] = (
    ("note_decision", NoteDecision),
    ("note_pin", NotePin),
    ("project_link", ProjectLink),
    ("project_entry", ProjectEntry),
    ("project_alias", ProjectAlias),
)
RAW: tuple[tuple[str, type[SQLModel]], ...] = (
    ("git_source", GitSource),
    ("git_network", GitNetwork),
    ("raw_transcript", RawTranscript),
    ("upload", Upload),
)
STORAGE_DIRS = ("transcripts", "repos", "uploads")
DERIVED_NAMES = (LOG_DIR, OVERVIEW_FILE)
MEMORY_NAMES = (MEMORY_DIR, ARCHIVE_DIR)


def cleared(directory: Path) -> int:
    if not directory.is_dir():
        return 0
    held = sorted(directory.iterdir())
    for path in held:
        shutil.rmtree(path) if path.is_dir() else path.unlink()
    return len(held)


def derived(projects: Path, *, memories: bool) -> int:
    if not projects.is_dir():
        return 0
    removed = 0
    for project in sorted(path for path in projects.iterdir() if path.is_dir()):
        for name in DERIVED_NAMES + (MEMORY_NAMES if memories else ()):
            target = project / name
            if target.is_dir():
                shutil.rmtree(target)
                removed += 1
            elif target.is_file():
                target.unlink()
                removed += 1
        if not any(project.iterdir()):
            project.rmdir()
    return removed


async def wipe(session: AsyncSession, *, decisions: bool, raw: bool, memories: bool) -> dict[str, int]:
    jobs = delete(Job) if memories else delete(Job).where(col(Job.kind) != JobKind.MEMORY_MERGE)
    done: dict[str, int] = {"job": int((await session.exec(jobs)).rowcount or 0)}
    for name, model in (DECIDED if decisions else ()) + (RAW if raw else ()):
        result = await session.exec(delete(model))
        done[name] = int(result.rowcount or 0)
    await session.commit()
    return done


def reset(
    *,
    decisions: bool = typer.Option(default=False, help="also forget merges, links, project scopes, pins and deletions."),
    memories: bool = typer.Option(default=False, help="also delete the memory notes, which no transcript can write again."),
    raw: bool = typer.Option(default=False, help="also delete the stored transcripts and git repositories, which cannot be recovered."),
    yes: bool = typer.Option(default=False, help="do not ask."),
) -> None:
    """Delete the notes and jobs so they can be built again from the stored transcripts."""
    settings = get_settings()
    going = ["the session notes, overviews and their jobs"]
    going += ["the memory notes, which nothing can write again"] * memories
    going += ["the stored transcripts and git repositories"] * raw
    going += ["the merges, links, project scopes, pins and deletions"] * decisions
    if not yes and not typer.confirm(f"Under {settings.notes_dir}, delete {', and '.join(going)}?"):
        raise typer.Abort

    async def main() -> dict[str, int]:
        try:
            async with settings.async_session_maker() as session:
                return await wipe(session, decisions=decisions, raw=raw, memories=memories)
        finally:
            await settings.async_engine.dispose()

    done = asyncio.run(main())
    done["notes"] = derived(settings.notes_dir / PROJECTS_ROOT, memories=memories)
    if raw:
        done["storage"] = sum(cleared(settings.storage_dir / name) for name in STORAGE_DIRS)
    typer.echo(", ".join(f"{name}: {count}" for name, count in done.items() if count))
    typer.echo("now POST /api/raw/rebuild to summarize the stored transcripts again." if not raw else "now re-import from every device.")
