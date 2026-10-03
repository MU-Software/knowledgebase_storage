from __future__ import annotations

import logging
from pathlib import Path

import typer

from backend.consts.sandbox import SANDBOX_SOCKET
from backend.sandbox import serve


def sandbox(socket: str = typer.Option(str(SANDBOX_SOCKET), help="Unix socket the api sends file commands to.")) -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    serve(Path(socket))
