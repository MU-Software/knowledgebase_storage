from __future__ import annotations

import asyncio
import sqlite3
import time
from datetime import UTC, datetime
from hashlib import sha256
from typing import TYPE_CHECKING, Annotated, BinaryIO
from uuid import uuid4

from fastapi import Depends

from backend.errors import ClientError
from backend.models import Blob
from backend.repositories.blob import BlobRepository, blobRepositoryDI, worktreeFileRepositoryDI
from backend.repositories.git import OUTPUT_CHARS, clip
from backend.repositories.storage import storageRepositoryDI
from backend.schemas import BlobManifestPart, BlobState
from backend.services import ServiceImpl

if TYPE_CHECKING:
    from pathlib import Path

    from backend.schemas import GitQuery

MAX_CHUNK_BYTES = 8 << 20
SNIFF_BYTES = 8192
HEX_BYTES = 256
QUERY_SECONDS = 10
ROW_LIMIT = 200
CELL_CHARS = 200
VALUE_BYTES = 16 << 20
LINE_CHARS = 2000
SQLITE_MAGIC = b"SQLite format 3\x00"
BLOCKED_ACTIONS = {sqlite3.SQLITE_ATTACH, sqlite3.SQLITE_DETACH}
MAGICS = {
    b"%PDF": "PDF document",
    b"PK\x03\x04": "zip archive",
    b"\x1f\x8b": "gzip data",
    b"\x7fELF": "ELF binary",
    b"Rar!": "RAR archive",
    b"\x89PNG": "PNG image",
    b"\xff\xd8\xff": "JPEG image",
}


def cell(value: object) -> str:
    if isinstance(value, bytes):
        return f"<{len(value)} bytes>"
    text = str(value)
    return text if len(text) <= CELL_CHARS else text[:CELL_CHARS] + "…"


def sqlite_text(path: Path, sql: str) -> str:
    connection = sqlite3.connect(f"{path.as_uri()}?mode=ro&immutable=1", uri=True)
    deadline = time.monotonic() + QUERY_SECONDS
    connection.set_progress_handler(lambda: int(time.monotonic() > deadline), 10000)
    connection.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, VALUE_BYTES)
    try:
        connection.execute("pragma query_only = on")
        connection.set_authorizer(lambda action, *_: sqlite3.SQLITE_DENY if action in BLOCKED_ACTIONS else sqlite3.SQLITE_OK)
        if sql:
            cursor = connection.execute(sql)
            rows = cursor.fetchmany(ROW_LIMIT + 1)
            lines = ["\t".join(column[0] for column in cursor.description or []), *("\t".join(map(cell, row)) for row in rows[:ROW_LIMIT])]
            return "\n".join(lines + [f"… more than {ROW_LIMIT} rows"] * (len(rows) > ROW_LIMIT))
        listed = connection.execute("select type, name, sql from sqlite_master where name not like 'sqlite_%' order by type, name").fetchall()
        counts = []
        for kind, name, _ in listed:
            if kind == "table":
                quoted = name.replace('"', '""')
                try:
                    counts.append(f"{name}: {connection.execute(f'select count(*) from "{quoted}"').fetchone()[0]} rows")  # noqa: S608
                except sqlite3.Error as exc:
                    counts.append(f"{name}: rows not counted ({exc})")
        schema = "\n".join(statement for _, _, statement in listed if statement)
        return "SQLite database. Pass sql to run a read-only query.\n\ntables:\n" + "\n".join(counts) + f"\n\nschema:\n{schema}"
    except sqlite3.Error as exc:
        return f"query failed: {exc}"
    finally:
        connection.close()


def text_lines(path: Path, start: int, end: int) -> str:
    shown: list[str] = []
    size = 0
    with path.open(encoding="utf-8", errors="replace") as handle:
        for number in range(1, end + 1):
            line = handle.readline(LINE_CHARS)
            if not line or size > OUTPUT_CHARS:
                break
            cut = len(line) == LINE_CHARS and not line.endswith("\n")
            while cut and (rest := handle.readline(LINE_CHARS)) and not rest.endswith("\n"):
                pass
            if number >= start:
                shown.append(f"{number}: {line.rstrip()}{'…' * cut}")
                size += len(shown[-1]) + 1
    return "\n".join(shown) or "no lines in that range"


def file_text(path: Path, payload: GitQuery) -> str:
    with path.open("rb") as handle:
        head = handle.read(SNIFF_BYTES)
    if head.startswith(SQLITE_MAGIC):
        return sqlite_text(path, payload.sql)
    kind = next((name for magic, name in MAGICS.items() if head.startswith(magic)), None)
    if kind is None and b"\x00" not in head:
        return text_lines(path, payload.start, payload.end)
    shown = head[:HEX_BYTES]
    rows = (f"{offset:08x}  {shown[offset : offset + 16].hex(' ')}" for offset in range(0, len(shown), 16))
    return f"{kind or 'binary data'}, {path.stat().st_size} bytes. The first {len(shown)} bytes:\n" + "\n".join(rows)


class BlobService(ServiceImpl[BlobRepository]):
    repository: blobRepositoryDI
    files: worktreeFileRepositoryDI
    storage: storageRepositoryDI

    async def missing(self, digests: list[str]) -> list[str]:
        return await asyncio.to_thread(lambda: [digest for digest in dict.fromkeys(digests) if not self.storage.exists(Blob.chunk_path_of(digest))])

    async def put_chunk(self, digest: str, payload: bytes) -> int:
        if len(payload) > MAX_CHUNK_BYTES:
            ClientError.CHUNK_TOO_LARGE.format_msg(limit=MAX_CHUNK_BYTES).raise_()
        if await asyncio.to_thread(lambda: sha256(payload).hexdigest()) != digest:
            ClientError.UPLOAD_DIGEST_MISMATCH.raise_()
        await asyncio.to_thread(self.storage.write_whole, Blob.chunk_path_of(digest), payload)
        return len(payload)

    async def incomplete(self, digests: list[str]) -> set[str]:
        return set(digests) - await self.repository.completed(digests)

    async def completed(self, digest: str) -> Blob:
        blob = await self.repository.find(digest)
        if blob is None or blob.completed_at is None:
            ClientError.RESOURCE_NOT_FOUND.format_msg(resource=self.repository.resource).raise_()
        return blob

    @staticmethod
    def state_of(blob: Blob) -> BlobState:
        return BlobState(digest=blob.digest, byte_size=blob.byte_size, chunk_count=len(blob.chunks), completed_at=blob.completed_at)

    async def add_manifest(self, digest: str, offset: int, part: BlobManifestPart) -> BlobState:
        blob = await self.repository.claim(digest)
        if blob.completed_at is not None:
            return self.state_of(blob)
        if offset == 0:
            blob.chunks = []
        if offset != len(blob.chunks):
            ClientError.BLOB_INCOMPLETE.format_msg(digest=digest, detail=f"the manifest holds {len(blob.chunks)} chunks, not {offset}").raise_()
        blob.byte_size = part.byte_size
        blob.chunks = [*blob.chunks, *part.chunks]
        return self.state_of(await self.repository.save(blob))

    async def complete(self, digest: str) -> BlobState:
        blob = await self.repository.find(digest)
        if blob is None:
            ClientError.RESOURCE_NOT_FOUND.format_msg(resource=self.repository.resource).raise_()
        if blob.completed_at is not None:
            return self.state_of(blob)
        if absent := await self.missing(blob.chunks):
            ClientError.BLOB_INCOMPLETE.format_msg(digest=digest, detail=f"{len(absent)} chunks were never uploaded").raise_()
        chunks = list(blob.chunks)
        total, whole = await asyncio.to_thread(self.digest_of, chunks)
        if total != blob.byte_size or whole != digest:
            await asyncio.to_thread(self.drop_damaged, chunks)
            detail = f"the chunks hold {total} bytes, not {blob.byte_size}" if total != blob.byte_size else "the chunks add up to a different file"
            ClientError.BLOB_INCOMPLETE.format_msg(digest=digest, detail=detail).raise_()
        locked = await self.repository.find(digest, with_for_update=True)
        if locked is None or locked.chunks != chunks:
            ClientError.BLOB_INCOMPLETE.format_msg(digest=digest, detail="the chunk list changed while it was checked").raise_()
        if locked.completed_at is None:
            locked.completed_at = datetime.now(UTC)
            locked = await self.repository.save(locked)
        return self.state_of(locked)

    def digest_of(self, chunks: list[str], sink: BinaryIO | None = None) -> tuple[int, str]:
        running, total = sha256(), 0
        for chunk in chunks:
            payload = self.storage.contain(Blob.chunk_path_of(chunk)).read_bytes()
            running.update(payload)
            total += len(payload)
            if sink is not None:
                sink.write(payload)
        return total, running.hexdigest()

    def drop_damaged(self, chunks: list[str]) -> None:
        for chunk in set(chunks):
            path = self.storage.contain(Blob.chunk_path_of(chunk))
            if path.is_file() and sha256(path.read_bytes()).hexdigest() != chunk:
                path.unlink()

    def assemble(self, blob: Blob) -> Path | None:
        target = self.storage.contain(Blob.path_of(blob.digest))
        if target.is_file():
            return target
        target.parent.mkdir(parents=True, exist_ok=True)
        staging = target.with_name(f"{target.name}.{uuid4().hex}.tmp")
        try:
            with staging.open("wb") as handle:
                _, whole = self.digest_of(blob.chunks, handle)
        except FileNotFoundError:
            whole = ""
        except OSError:
            staging.unlink(missing_ok=True)
            raise
        if whole != blob.digest:
            staging.unlink()
            self.drop_damaged(blob.chunks)
            return None
        staging.replace(target)
        return target

    async def inspect(self, digest: str, payload: GitQuery) -> str:
        blob = await self.repository.find(digest)
        if blob is None or blob.completed_at is None:
            return "this file is being uploaded again; it can be read after the device that holds it uploads it"
        path = await asyncio.to_thread(self.assemble, blob)
        if path is None:
            blob.completed_at = None
            await self.repository.save(blob)
            return "the stored chunks do not add up to this file; it will be uploaded again"
        try:
            return clip(await asyncio.to_thread(file_text, path, payload))
        except FileNotFoundError:
            return "this file was replaced while it was being read; ask again"

    async def release(self, digests: set[str]) -> None:
        for digest in digests - await self.files.referenced(list(digests)):
            self.storage.remove(Blob.path_of(digest))


blobServiceDI = Annotated[BlobService, Depends(BlobService)]  # noqa: N816
