"""Install and remove pi packages (extensions, skills, prompts) for the pi engine.

pi owns the install: ``pi install`` / ``pi remove`` run with ``PI_CODING_AGENT_DIR``
pointing at ``pi_engine/agent-dir`` and write the ``packages`` list in that
directory's ``settings.json``. ``pi_engine/loop.ts`` builds its resource loader
from the same directory on every turn, so a new package is live on the next turn.

A package runs code in the engine process and can carry skills that tell the
model to run programs, so every entry point here is admin-only and the source
is restricted to forms ``pi`` documents. Nothing is executed through a shell.
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import re
from typing import Any

PI_ENGINE_DIR = Path(__file__).resolve().parents[2] / "pi_engine"
AGENT_DIR = PI_ENGINE_DIR / "agent-dir"
_PI_BIN = PI_ENGINE_DIR / "node_modules" / ".bin" / "pi"

INSTALL_TIMEOUT_S = 180
_NPM = re.compile(r"^npm:(@[a-z0-9][\w.-]*/)?[a-z0-9][\w.-]*(@[\w.^~<>=*-]+)?$", re.I)
_GIT = re.compile(r"^git:[\w.-]+(\.[\w.-]+)+/[\w./~-]+(@[\w./-]+)?$")
_URL = re.compile(r"^https://[\w.-]+(:\d+)?/[\w./~%-]+$")

_lock = asyncio.Lock()


class PiPackageError(ValueError):
    """The request cannot be carried out."""


def validate_source(source: str) -> str:
    """Return a normalized package source or raise :class:`PiPackageError`."""
    value = str(source or "").strip()
    if not value or len(value) > 300 or value.startswith("-") or any(ord(c) < 32 for c in value):
        raise PiPackageError("Enter a package source such as npm:@scope/name or git:github.com/owner/repo.")
    if _NPM.match(value) or _GIT.match(value) or _URL.match(value):
        return value
    path = Path(value).expanduser()
    if path.is_absolute() and path.is_dir():
        return str(path)
    raise PiPackageError("Use an npm:, git: or https:// source, or the absolute path of a local package folder.")


def _settings_path() -> Path:
    return AGENT_DIR / "settings.json"


def list_packages() -> list[dict[str, Any]]:
    """The packages recorded in the engine's settings, as ``{source, filtered}``."""
    try:
        data = json.loads(_settings_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    out: list[dict[str, Any]] = []
    for item in data.get("packages") or []:
        if isinstance(item, str):
            out.append({"source": item, "filtered": False})
        elif isinstance(item, dict) and isinstance(item.get("source"), str):
            out.append({"source": item["source"], "filtered": True})
    return out


async def _run(*args: str) -> str:
    if not _PI_BIN.exists():
        raise PiPackageError("The pi engine is not installed. Run bun install in pi_engine first.")
    env = {**os.environ, "PI_CODING_AGENT_DIR": str(AGENT_DIR), "CI": "1"}
    async with _lock:
        proc = await asyncio.create_subprocess_exec(
            str(_PI_BIN), *args,
            cwd=str(PI_ENGINE_DIR), env=env, stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
        )
        try:
            out, _ = await asyncio.wait_for(proc.communicate(), INSTALL_TIMEOUT_S)
        except asyncio.TimeoutError as exc:
            proc.kill()
            await proc.wait()
            raise PiPackageError("The package command timed out.") from exc
    text = out.decode("utf-8", "replace").strip()[-2000:]
    if proc.returncode != 0:
        raise PiPackageError(text or "The package command failed.")
    return text


async def install_package(source: str) -> str:
    return await _run("install", validate_source(source))


async def remove_package(source: str) -> str:
    value = str(source or "").strip()
    if value not in {p["source"] for p in list_packages()}:
        raise PiPackageError("That package is not installed.")
    if value.startswith((".", "/", "~")):  # local paths are stored relative to settings.json
        value = str((AGENT_DIR / Path(value).expanduser()).resolve())
    return await _run("remove", value)
