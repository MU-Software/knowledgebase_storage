from __future__ import annotations

import asyncio
import json
from typing import Annotated

from fastapi import Depends

from backend.dependencies import sandboxSocketDI
from backend.repositories import RepositoryImpl

REQUEST_SECONDS = 55
ANSWER_BYTES = 1 << 20


class SandboxRepository(RepositoryImpl):
    socket: sandboxSocketDI

    resource = "file runner"

    async def run(self, path: str, name: str, script: str) -> str:
        try:
            async with asyncio.timeout(REQUEST_SECONDS):
                reader, writer = await asyncio.open_unix_connection(self.socket.as_posix(), limit=ANSWER_BYTES)
                try:
                    writer.write(json.dumps({"path": path, "name": name, "script": script}).encode() + b"\n")
                    await writer.drain()
                    answer = json.loads(await reader.readline())
                finally:
                    writer.close()
        except (OSError, TimeoutError, ValueError) as exc:
            return f"the file runner did not answer: {str(exc) or type(exc).__name__}"
        return str(answer.get("output", ""))


sandboxRepositoryDI = Annotated[SandboxRepository, Depends(SandboxRepository)]  # noqa: N816
