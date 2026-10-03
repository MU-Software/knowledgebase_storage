from __future__ import annotations

from hashlib import sha256
from typing import TYPE_CHECKING, Annotated
from uuid import uuid4

from fastapi import Depends

from backend.dependencies import storageDirDI
from backend.errors import ClientError
from backend.repositories import RepositoryImpl

if TYPE_CHECKING:
    from pathlib import Path

READ_CHUNK = 1 << 20


class StorageRepository(RepositoryImpl):
    root: storageDirDI

    resource = "stored file"

    def contain(self, relative_path: str) -> Path:
        target = (self.root / relative_path).resolve()
        if not target.is_relative_to(self.root):
            ClientError.INVALID_STORAGE_PATH.format_msg(path=relative_path).raise_()
        return target

    def size(self, relative_path: str) -> int:
        target = self.root / relative_path
        return target.stat().st_size if target.is_file() else 0

    def append(self, relative_path: str, payload: bytes, offset: int) -> int:
        target = self.root / relative_path
        self.contain(relative_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        current = target.stat().st_size if target.is_file() else 0
        if offset != current:
            ClientError.UPLOAD_OFFSET_MISMATCH.format_msg(expected=current, given=offset).raise_()
        with target.open("ab") as handle:
            handle.write(payload)
        return current + len(payload)

    def exists(self, relative_path: str) -> bool:
        return self.contain(relative_path).is_file()

    def write_whole(self, relative_path: str, payload: bytes) -> None:
        target = self.contain(relative_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        staging = target.with_name(f"{target.name}.{uuid4().hex}.tmp")
        staging.write_bytes(payload)
        staging.replace(target)

    def truncate(self, relative_path: str) -> None:
        target = self.root / relative_path
        self.contain(relative_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"")

    def digest(self, relative_path: str) -> str:
        target = self.root / relative_path
        if not target.is_file():
            return ""
        running = sha256()
        with target.open("rb") as handle:
            while block := handle.read(READ_CHUNK):
                running.update(block)
        return running.hexdigest()

    def remove(self, relative_path: str) -> bool:
        target = self.root / relative_path
        self.contain(relative_path)
        if not target.is_file():
            return False
        target.unlink()
        return True


storageRepositoryDI = Annotated[StorageRepository, Depends(StorageRepository)]  # noqa: N816
