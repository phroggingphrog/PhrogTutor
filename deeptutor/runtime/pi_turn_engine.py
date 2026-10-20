"""pi SDK turn engine (TASK-71 step B).

One ``createAgentSession`` per chat turn, run by ``pi_engine/turn.ts`` (bun). The script prints one
JSON line per event; this adapter turns them into the same StreamEvents the orchestrator emits:
session, content, tool_call, tool_result, error, done. Enabled with ``PI_TUTOR_ENGINE=1``.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
import json
import os
from pathlib import Path
import shutil
from typing import Any
import uuid

from deeptutor.core.context import UnifiedContext
from deeptutor.core.stream import StreamEvent, StreamEventType

_TURN_TS = Path(__file__).resolve().parents[2] / "pi_engine" / "turn.ts"
_SRC = "pi_tutor"


def pi_engine_enabled() -> bool:
    return os.environ.get("PI_TUTOR_ENGINE", "").lower() in ("1", "true", "yes")


async def run_pi_turn(context: UnifiedContext) -> AsyncIterator[StreamEvent]:
    if not context.session_id:
        context.session_id = str(uuid.uuid4())
    turn_id = str(context.metadata.get("turn_id", ""))
    yield StreamEvent(
        type=StreamEventType.SESSION,
        source="orchestrator",
        metadata={"session_id": context.session_id, "turn_id": turn_id},
    )

    payload: dict[str, Any] = {
        "message": context.user_message,
        "history": [
            {"role": m.get("role", "user"), "content": m.get("content", "")}
            for m in (context.conversation_history or [])
        ],
        "system": getattr(context, "persona_context", "") or "",
    }
    status = "completed"
    proc = await asyncio.create_subprocess_exec(
        shutil.which("bun") or "bun",
        str(_TURN_TS),
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    assert proc.stdin and proc.stdout
    proc.stdin.write(json.dumps(payload).encode())
    await proc.stdin.drain()
    proc.stdin.close()
    stderr_chunks: list[bytes] = []

    async def _drain_stderr() -> None:  # keep the pipe empty so the child never blocks on stderr
        assert proc.stderr
        async for line in proc.stderr:
            stderr_chunks.append(line)

    drain = asyncio.create_task(_drain_stderr())
    try:
        async for raw in proc.stdout:
            try:
                e = json.loads(raw)
            except ValueError:
                continue
            kind = e.get("type")
            if kind == "content":
                yield StreamEvent(type=StreamEventType.CONTENT, source=_SRC, content=e.get("content", ""))
            elif kind == "tool_call":
                yield StreamEvent(
                    type=StreamEventType.TOOL_CALL,
                    source=_SRC,
                    content=e.get("tool", ""),
                    metadata={"args": e.get("args") or {}},
                )
            elif kind == "tool_result":
                yield StreamEvent(
                    type=StreamEventType.TOOL_RESULT,
                    source=_SRC,
                    content=e.get("content", ""),
                    metadata={"tool": e.get("tool", ""), "is_error": bool(e.get("is_error"))},
                )
            elif kind == "error":
                status = "failed"
                yield StreamEvent(
                    type=StreamEventType.ERROR,
                    source=_SRC,
                    content=e.get("content", ""),
                    metadata={"turn_terminal": True, "status": "failed"},
                )
            elif kind == "done" and e.get("status") != "completed":
                status = "failed"
        await drain
        stderr = b"".join(stderr_chunks).decode(errors="replace").strip()
        if status != "failed" and proc.returncode not in (0, None):
            status = "failed"
            yield StreamEvent(
                type=StreamEventType.ERROR,
                source=_SRC,
                content=f"pi turn exited with code {proc.returncode}: {stderr[-500:]}",
                metadata={"turn_terminal": True, "status": "failed"},
            )
    finally:
        if proc.returncode is None:
            proc.kill()
        await proc.wait()
        if not drain.done():
            drain.cancel()
    yield StreamEvent(type=StreamEventType.DONE, source=_SRC, metadata={"status": status})
