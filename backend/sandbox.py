from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import re
import shutil
import signal
import tempfile
import time
from pathlib import Path, PurePosixPath
from subprocess import DEVNULL, PIPE, STDOUT

logger = logging.getLogger(__name__)

STORAGE_DIR = Path(os.environ.get("STORAGE_DIR", "/srv/knowledgebase/storage")).resolve()
SCRATCH = Path(tempfile.gettempdir())
WAIT_SECONDS = 10
REQUEST_BYTES = 1 << 20
COMMAND_SECONDS = 30
REAP_SECONDS = 5
WATCH_SECONDS = 30
CPU_SECONDS = 60
FILE_BYTES = 512 << 20
OUTPUT_BYTES = 24 << 10
READ_BYTES = 1 << 16
ENVIRONMENT = {"PATH": "/usr/local/bin:/usr/bin:/bin", "LANG": "C.UTF-8"}
PREAMBLE = "echo 1000 2>/dev/null >/proc/self/oom_score_adj\n"


async def collect(process: asyncio.subprocess.Process, stream: asyncio.StreamReader) -> tuple[bytes, str]:
    output = bytearray()
    try:
        async with asyncio.timeout(COMMAND_SECONDS):
            while block := await stream.read(READ_BYTES):
                output += block
                if len(output) > OUTPUT_BYTES:
                    return bytes(output[:OUTPUT_BYTES]), f"output cut at {OUTPUT_BYTES} bytes"
            await process.wait()
    except TimeoutError:
        return bytes(output), f"stopped after {COMMAND_SECONDS} seconds"
    return bytes(output), f"exit {process.returncode}" if process.returncode else ""


def sweep() -> None:
    if os.getpid() != 1:
        return
    for entry in Path("/proc").iterdir():
        if entry.name.isdigit() and int(entry.name) != 1:
            with contextlib.suppress(OSError):
                os.kill(int(entry.name), signal.SIGKILL)
    with contextlib.suppress(ChildProcessError):
        while os.waitpid(-1, os.WNOHANG)[0]:
            pass
    for entry in SCRATCH.iterdir():
        if entry.is_dir() and not entry.is_symlink():
            shutil.rmtree(entry, ignore_errors=True)
        else:
            entry.unlink(missing_ok=True)


async def run(path: str, name: str, script: str) -> str:
    target = (STORAGE_DIR / path).resolve()
    if not target.is_relative_to(STORAGE_DIR) or not target.is_file():
        return "the file is not in storage; it may have just been replaced, so ask again"
    workdir = Path(tempfile.mkdtemp(prefix="run-"))
    try:
        suffix = PurePosixPath(name).suffix
        shown = workdir / "input" / f"file{suffix if re.fullmatch(r'\.[A-Za-z0-9]{1,10}', suffix) else ''}"
        shown.parent.mkdir()
        shown.symlink_to(target)
        process = await asyncio.create_subprocess_exec(
            "prlimit",
            f"--cpu={CPU_SECONDS}",
            f"--fsize={FILE_BYTES}",
            "sh",
            "-c",
            PREAMBLE + script.replace("{file}", shown.as_posix()),
            cwd=workdir,
            env={**ENVIRONMENT, "HOME": workdir.as_posix(), "TMPDIR": workdir.as_posix()},
            stdin=DEVNULL,
            stdout=PIPE,
            stderr=STDOUT,
            start_new_session=True,
        )
        try:
            output, note = await collect(process, process.stdout) if process.stdout else (b"", "")
        finally:
            with contextlib.suppress(ProcessLookupError):
                os.killpg(process.pid, signal.SIGKILL)
            with contextlib.suppress(TimeoutError):
                async with asyncio.timeout(REAP_SECONDS):
                    await process.wait()
        text = output.decode("utf-8", errors="replace")
        return f"({note})\n{text}" if note else text
    finally:
        await asyncio.to_thread(shutil.rmtree, workdir, ignore_errors=True)
        await asyncio.to_thread(sweep)


def same_socket(socket: Path, identity: int) -> bool:
    try:
        return socket.stat().st_ino == identity
    except FileNotFoundError:
        return False


async def listen(socket: Path) -> None:
    lock = asyncio.Lock()

    async def answer(request: dict[str, object]) -> str:
        try:
            async with asyncio.timeout(WAIT_SECONDS):
                await lock.acquire()
        except TimeoutError:
            return "another command is still running; try again shortly"
        try:
            return await run(str(request["path"]), str(request["name"]), str(request["script"]))
        finally:
            lock.release()

    async def watch() -> None:
        while True:
            await asyncio.sleep(WATCH_SECONDS)
            if not await asyncio.to_thread(same_socket, socket, identity):
                logger.error("the socket was removed or replaced; exiting so the container restarts")
                os._exit(1)

    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        started, script = time.monotonic(), ""
        try:
            request = json.loads(await reader.readline())
            script = str(request["script"])
            output = await answer(request)
        except (ValueError, KeyError, TypeError, OSError) as exc:
            output = f"the runner could not take the request: {exc}"
        logger.info("ran %r in %.1fs, %d chars", script[:200], time.monotonic() - started, len(output))
        try:
            writer.write(json.dumps({"output": output}).encode() + b"\n")
            await writer.drain()
        except ConnectionError:
            logger.info("the caller left before the answer")
        finally:
            writer.close()

    server = await asyncio.start_unix_server(handle, path=socket.as_posix(), limit=REQUEST_BYTES)
    identity = (await asyncio.to_thread(socket.stat)).st_ino
    watcher = asyncio.create_task(watch())
    asyncio.get_running_loop().add_signal_handler(signal.SIGTERM, server.close)
    logger.info("listening on %s, reading %s", socket, STORAGE_DIR)
    async with server:
        with contextlib.suppress(asyncio.CancelledError):
            await server.serve_forever()
    watcher.cancel()


def serve(socket: Path) -> None:
    socket.parent.mkdir(parents=True, exist_ok=True)
    socket.unlink(missing_ok=True)
    asyncio.run(listen(socket))
