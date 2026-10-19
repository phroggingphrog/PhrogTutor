"""map_project: map any codebase (a local folder or a git URL) with Graphify so the tutor can give guided tours.

Runs the `project-map` command (Graphify pinned, code only, no AI cost, on a copy). A git URL is shallow-cloned into
pi-tutor's own data folder first; the repo's code is only parsed, never run. Maps land in data/user/project-maps/.
"""

from __future__ import annotations

import asyncio
import os
import re
import shutil
from pathlib import Path
from typing import Any

from deeptutor.core.tool_protocol import BaseTool, ToolDefinition, ToolParameter, ToolResult
from deeptutor.services.path_service import get_path_service

_CMD = os.environ.get("PI_TUTOR_PROJECT_MAP_BIN", str(Path.home() / ".local/bin/project-map"))


class MapProjectTool(BaseTool):
    def get_definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="map_project",
            description=(
                "Map a codebase so you can tour it with the learner: a local folder path or a git URL "
                "(https://github.com/...). Takes up to a few minutes for big repos. Returns the project map "
                "(main areas, their files and how they connect). Use when the learner wants to understand a project "
                "that is not in the list of mapped projects yet."
            ),
            parameters=[
                ToolParameter(name="source", type="string", description="Folder path or git URL."),
                ToolParameter(name="name", type="string", description="Short project name (lowercase-hyphen).", required=False),
            ],
        )

    async def execute(self, **kwargs: Any) -> ToolResult:
        source = str(kwargs.get("source") or "").strip()
        name = re.sub(r"[^a-z0-9-]+", "-", str(kwargs.get("name") or Path(source.rstrip("/")).name.removesuffix(".git")).lower()).strip("-") or "project"
        data = get_path_service().user_data_dir
        known = data / "project-maps" / f"{source.lower()}.md"
        if known.is_file():  # already mapped: return it straight away
            return ToolResult(content=known.read_text(encoding="utf-8")[:8000], metadata={"project": source.lower()})
        if re.match(r"^(https://|git@)", source):
            dest = data / "project-sources" / name
            shutil.rmtree(dest, ignore_errors=True)
            dest.parent.mkdir(parents=True, exist_ok=True)
            proc = await asyncio.create_subprocess_exec(
                "git", "clone", "--depth", "1", "--quiet", source, str(dest),
                env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            )
            _, err = await asyncio.wait_for(proc.communicate(), timeout=300)
            if proc.returncode:
                return ToolResult(content=f"Could not clone {source}: {err.decode()[-300:]}", success=False)
            folder = dest
        else:
            folder = Path(source).expanduser()
            if not folder.is_dir():
                return ToolResult(content=f"No folder at {source}", success=False)
        proc = await asyncio.create_subprocess_exec(_CMD, str(folder), name, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        out, err = await asyncio.wait_for(proc.communicate(), timeout=900)
        if proc.returncode:
            return ToolResult(content=f"Mapping failed: {err.decode()[-400:]}", success=False)
        text = Path(out.decode().strip().splitlines()[-1]).read_text(encoding="utf-8")
        return ToolResult(content=text[:8000], metadata={"project": name})


class ReadProjectFileTool(BaseTool):
    """Read-only view into a mapped project's real code, limited to that project's folder."""

    def get_definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="read_project_file",
            description=(
                "Read real code from a mapped project (read-only), to quote actual lines during a tour. "
                "path is relative to the project root, as shown in the project map. Leave path empty to list folders."
            ),
            parameters=[
                ToolParameter(name="project", type="string", description="Mapped project name, e.g. jobwatch."),
                ToolParameter(name="path", type="string", description="File or folder path inside the project.", required=False),
                ToolParameter(name="start", type="integer", description="First line (1-based).", required=False),
                ToolParameter(name="lines", type="integer", description="How many lines (max 300).", required=False),
            ],
        )

    async def execute(self, **kwargs: Any) -> ToolResult:
        project = str(kwargs.get("project") or "").lower()
        mp = get_path_service().user_data_dir / "project-maps" / f"{project}.md"
        if not mp.is_file():
            return ToolResult(content=f"No mapped project '{project}'. Call map_project first.", success=False)
        m = re.search(r"^Source: (\S+)", mp.read_text(encoding="utf-8"), re.M)
        root = Path(m.group(1)).resolve()
        rel = str(kwargs.get("path") or "").strip().lstrip("/")
        target = (root / rel).resolve()
        if target != root and root not in target.parents or any(p.startswith(".") for p in Path(rel).parts) or re.search(r"(^|/)(\.env|.*secret.*|.*\.key|.*token.*)$", rel, re.I):
            return ToolResult(content="That path is outside the project or private.", success=False)
        if target.is_dir():
            items = sorted(p.name + ("/" if p.is_dir() else "") for p in target.iterdir() if not p.name.startswith("."))
            return ToolResult(content="\n".join(items[:200]))
        if not target.is_file() and (root / "src" / rel).is_file():  # map paths sometimes drop a leading src/
            target = root / "src" / rel
        if not target.is_file():
            return ToolResult(content=f"No file {rel}", success=False)
        start = max(1, int(kwargs.get("start") or 1))
        n = min(300, int(kwargs.get("lines") or 150))
        text = target.read_text(encoding="utf-8", errors="replace").splitlines()
        body = "\n".join(f"{i}: {t}" for i, t in enumerate(text[start - 1 : start - 1 + n], start))
        return ToolResult(content=f"{rel} (lines {start}-{min(len(text), start + n - 1)} of {len(text)})\n{body}")
