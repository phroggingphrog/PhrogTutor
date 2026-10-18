"""Terminal panel: a bash shell in the same bwrap sandbox as code cells (no network, memory cap).

The shell's home is a persistent folder per note (``$PI_TUTOR_RUNCODE_DIR/terminal-home/<note>``), so files
made there survive between sessions; nothing else on the host is writable. The shell is killed when the
page closes the connection or after 30 minutes without input.
"""

from __future__ import annotations

import asyncio
import fcntl
import json
import os
import re
import pty
import struct
import termios
from pathlib import Path

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from deeptutor.api.routers.run_code import PYLIBS, RUNCODE_DIR
from deeptutor.services.sandbox.backends import BwrapBackend
from deeptutor.services.sandbox.spec import ExecRequest, Mount

router = APIRouter()

HOME = RUNCODE_DIR / "terminal-home"
_IDLE_S = 30 * 60


def _home(note: str) -> Path:
    """One home folder per note (files made in a note's terminal stay with that note)."""
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", note).strip("._")[:120] or "_scratch"
    return HOME / safe


def _argv(home: Path) -> list[str]:
    home.mkdir(parents=True, exist_ok=True)
    argv = BwrapBackend(inherit_virtualenv=False)._build_argv(
        ExecRequest.of_argv(
            ["prlimit", "--as=3221225472", "--nproc=256", "--fsize=200000000", "--", "bash", "--norc", "-i"],
            workdir="/work",
            mounts=(Mount(str(home), "/work", read_only=False), Mount(str(PYLIBS), "/pylibs")),
            env={
                "HOME": "/work", "TERM": "xterm-256color", "PYTHONPATH": "/pylibs", "MPLBACKEND": "Agg",
                "PS1": r"\[\e[32m\]sandbox\[\e[0m\]:\w$ ",
            },
        )
    )
    # --new-session stops a sandbox from typing into its parent's terminal; here that terminal is the
    # sandbox's own pty, so dropping it is safe and gives the shell job control (Ctrl+C works).
    return [a for a in argv if a != "--new-session"]


@router.websocket("/terminal")
async def terminal(websocket: WebSocket):
    from deeptutor.api.routers.auth import ws_auth_failed, ws_require_auth

    if await ws_require_auth(websocket) is ws_auth_failed:
        return
    await websocket.accept()
    pid, fd = pty.fork()
    if pid == 0:  # child: become the sandboxed shell
        argv = _argv(_home(websocket.query_params.get("note", "")))
        os.execvp(argv[0], argv)
    loop = asyncio.get_running_loop()
    output: asyncio.Queue[bytes] = asyncio.Queue()
    loop.add_reader(fd, lambda: output.put_nowait(_read(fd)))

    async def pump() -> None:
        while (data := await output.get()):
            await websocket.send_bytes(data)
        await websocket.close()

    sender = asyncio.create_task(pump())
    try:
        while True:
            msg = await asyncio.wait_for(websocket.receive_text(), timeout=_IDLE_S)
            event = json.loads(msg)
            if event.get("type") == "input":
                os.write(fd, event["data"].encode())
            elif event.get("type") == "resize":
                fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", event["rows"], event["cols"], 0, 0))
    except (WebSocketDisconnect, asyncio.TimeoutError, OSError, RuntimeError):
        pass
    finally:
        loop.remove_reader(fd)
        sender.cancel()
        try:
            os.kill(pid, 9)
            os.waitpid(pid, 0)
        except (ProcessLookupError, ChildProcessError):
            pass
        os.close(fd)


def _read(fd: int) -> bytes:
    try:
        return os.read(fd, 65536)
    except OSError:
        return b""  # shell exited: the pump closes the socket
