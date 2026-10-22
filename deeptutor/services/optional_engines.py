"""Optional engines (pip extras) the Settings page can show and install.

Each engine is an extra declared in ``pyproject.toml``: its requirement list comes from there (or from the installed
package's metadata for a wheel install), so a user can only install what the project declares, never an arbitrary
package. An install runs ``pip install`` for the requirements that are not satisfied yet, in the interpreter running the
app. The choice is also written to ``data/user/settings/extras.json`` so ``scripts/install_extras.py`` can restore it when a
container is recreated, since a pip install into a container does not survive one.
"""

from __future__ import annotations

import asyncio
from collections import deque
import importlib
import json
import os
from pathlib import Path
import sys
import time
import tomllib
from typing import Any

from deeptutor.services.path_service import get_path_service

PYPROJECT = Path(__file__).resolve().parents[2] / "pyproject.toml"
INSTALL_TIMEOUT_S = 30 * 60

#: id == the extra's name in pyproject.toml.
ENGINES: tuple[dict[str, str], ...] = (
    {
        "id": "rag-llamaindex",
        "name": "LlamaIndex + FAISS",
        "description": "The default knowledge-base engine: hybrid keyword and vector retrieval.",
        "size": "~300 MB",
    },
    {
        "id": "rag-pageindex",
        "name": "PageIndex",
        "description": "Reasoning-based retrieval over long documents.",
        "size": "small",
    },
    {
        "id": "rag-lightrag",
        "name": "LightRAG",
        "description": "Graph-based knowledge-base engine.",
        "size": "medium",
    },
    {
        "id": "graphrag",
        "name": "GraphRAG",
        "description": "Microsoft GraphRAG knowledge graphs. Heavy; not available on Python 3.14.",
        "size": "large",
    },
    {
        "id": "partners",
        "name": "Partner channels",
        "description": "SDKs that connect Partners to Telegram, Slack, Discord, Feishu and other chat apps.",
        "size": "medium",
    },
    {
        "id": "math-animator",
        "name": "Math animator (Manim)",
        "description": "Renders math animations. Also needs LaTeX, ffmpeg, cairo and cmake on the machine.",
        "size": "large",
    },
)


class EngineError(ValueError):
    """The request cannot be carried out."""


def declared_extras() -> dict[str, list[str]]:
    """``{extra: [requirement, ...]}`` from the project file, or the installed package's metadata."""
    if PYPROJECT.exists():
        try:
            data = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))
            optional = data.get("project", {}).get("optional-dependencies", {})
            return {str(k): [str(r) for r in v] for k, v in optional.items() if isinstance(v, list)}
        except (OSError, ValueError):
            pass
    from importlib.metadata import PackageNotFoundError, metadata

    from packaging.requirements import Requirement

    try:
        lines = metadata("deeptutor").get_all("Requires-Dist") or []
    except PackageNotFoundError:
        return {}
    extras: dict[str, list[str]] = {}
    for line in lines:
        requirement = Requirement(line)
        if requirement.marker is None:
            continue
        for extra in _marker_extras(str(requirement.marker)):
            bare = Requirement(line)
            bare.marker = None
            extras.setdefault(extra, []).append(str(bare))
    return extras


def _marker_extras(marker: str) -> list[str]:
    import re

    return re.findall(r"extra\s*==\s*['\"]([^'\"]+)['\"]", marker)


def missing_requirements(requirements: list[str]) -> list[str]:
    """The requirements not satisfied in this interpreter (markers that do not apply here are skipped)."""
    from importlib.metadata import PackageNotFoundError, version

    from packaging.requirements import Requirement

    missing: list[str] = []
    for raw in requirements:
        try:
            requirement = Requirement(raw)
        except Exception:
            missing.append(raw)
            continue
        if requirement.marker is not None and not requirement.marker.evaluate():
            continue
        try:
            installed = version(requirement.name)
        except PackageNotFoundError:
            missing.append(raw)
            continue
        if requirement.specifier and installed not in requirement.specifier:
            missing.append(raw)
    return missing


def status() -> list[dict[str, Any]]:
    extras = declared_extras()
    rows = []
    for engine in ENGINES:
        requirements = extras.get(engine["id"])
        if requirements is None:
            continue  # this build does not declare the extra
        missing = missing_requirements(requirements)
        rows.append({**engine, "installed": not missing, "missing": len(missing)})
    return rows


# ---- install jobs --------------------------------------------------------------------------------------------------

_job: dict[str, Any] = {"engine": None, "state": "idle", "log": deque(maxlen=300), "started": None, "finished": None}
_lock = asyncio.Lock()


def job() -> dict[str, Any]:
    return {**_job, "log": list(_job["log"])}


def _settings_file() -> Path:
    return get_path_service().get_settings_file("extras")


def recorded_extras() -> list[str]:
    try:
        data = json.loads(_settings_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    names = data.get("extras") if isinstance(data, dict) else None
    return [n for n in names if isinstance(n, str)] if isinstance(names, list) else []


def _record(engine_id: str) -> None:
    names = recorded_extras()
    if engine_id not in names:
        names.append(engine_id)
    path = _settings_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"extras": names}, indent=2) + "\n", encoding="utf-8")


async def install(engine_id: str) -> dict[str, Any]:
    """Start installing one engine in the background; poll :func:`job` for progress."""
    if engine_id not in {e["id"] for e in ENGINES}:
        raise EngineError("Unknown engine.")
    requirements = declared_extras().get(engine_id)
    if requirements is None:
        raise EngineError("This build does not offer that engine.")
    if _job["state"] == "installing":
        raise EngineError("Another install is still running.")
    missing = missing_requirements(requirements)
    _job.update(engine=engine_id, state="installing", started=time.time(), finished=None)
    _job["log"].clear()
    if not missing:
        _job["log"].append("Already installed.")
        _job.update(state="done", finished=time.time())
        _record(engine_id)
        return job()
    asyncio.create_task(_run(engine_id, missing))
    return job()


async def _run(engine_id: str, missing: list[str]) -> None:
    async with _lock:
        _job["log"].append(f"Installing {len(missing)} package(s) for {engine_id} ...")
        try:
            proc = await asyncio.create_subprocess_exec(
                sys.executable, "-m", "pip", "install", "--no-input", *missing,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
                env={**os.environ, "PIP_DISABLE_PIP_VERSION_CHECK": "1"},
            )

            async def pump() -> None:
                assert proc.stdout is not None
                async for line in proc.stdout:
                    text = line.decode("utf-8", "replace").rstrip()
                    if text:
                        _job["log"].append(text[:300])

            await asyncio.wait_for(asyncio.gather(pump(), proc.wait()), INSTALL_TIMEOUT_S)
            ok = proc.returncode == 0
        except TimeoutError:
            ok = False
            _job["log"].append("Timed out.")
        except OSError as exc:
            ok = False
            _job["log"].append(f"Could not run pip: {exc}")
        if ok:
            importlib.invalidate_caches()
            _record(engine_id)
            _job["log"].append("Done. Restart the app if the engine does not appear straight away.")
        _job.update(state="done" if ok else "failed", finished=time.time())
