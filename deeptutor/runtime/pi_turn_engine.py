"""pi SDK turn engine (TASK-71 steps B and C).

One ``createAgentSession`` per chat turn, run by ``pi_engine/turn.ts`` (bun). The script prints one
JSON line per event; this adapter turns them into the same StreamEvents the orchestrator emits:
session, content, tool_call, tool_result, error, done. Enabled with ``PI_TUTOR_ENGINE=1``.

Step C (backend learning, no UI): the learner profile from ``pi_learning`` is injected into the
turn's system prompt, and one ``learning_event`` is appended per turn afterwards. Both are
best-effort and never fail the turn.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import uuid
from collections.abc import AsyncIterator
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from deeptutor.core.context import UnifiedContext
from deeptutor.core.stream import StreamEvent, StreamEventType
from deeptutor.runtime import pi_learning_hook

_TURN_TS = Path(__file__).resolve().parents[2] / "pi_engine" / "turn.ts"
_SRC = "pi_tutor"
logger = logging.getLogger(__name__)


def pi_engine_enabled() -> bool:
    return os.environ.get("PI_TUTOR_ENGINE", "").lower() in ("1", "true", "yes")


def _record_turn_safely(**kwargs: Any) -> None:
    """Append this turn's learning_event. Never raises: the log is not worth a failed turn."""
    try:
        if not kwargs.get("course_id"):
            kwargs["course_id"] = pi_learning_hook.course_id_for(
                kwargs.get("question", ""),
                " ".join(kwargs.get("wiki_pages") or []),
                " ".join(kwargs.get("result_text") or []),
            )
        kwargs.pop("result_text", None)
        pi_learning_hook.record_turn(**kwargs)
    except Exception:  # pragma: no cover - defensive, the hook already fails closed
        logger.exception("could not record the learning event for this turn")


async def run_pi_turn(context: UnifiedContext) -> AsyncIterator[StreamEvent]:
    if not context.session_id:
        context.session_id = str(uuid.uuid4())
    turn_id = str(context.metadata.get("turn_id", ""))
    yield StreamEvent(
        type=StreamEventType.SESSION,
        source="orchestrator",
        metadata={"session_id": context.session_id, "turn_id": turn_id},
    )

    question = context.user_message or ""
    history_text = "\n".join(
        str(m.get("content", "")) for m in (context.conversation_history or [])
    )
    # Step C: profile goes in before the turn; the course is resolved from what the turn already
    # shows (owner's words, history), and refined afterwards from the wiki pages actually read.
    course_id = await asyncio.to_thread(pi_learning_hook.course_id_for, question, history_text)
    profile = await asyncio.to_thread(pi_learning_hook.build_profile_text, course_id)
    payload: dict[str, Any] = {
        "message": question,
        "history": [
            {"role": m.get("role", "user"), "content": m.get("content", "")}
            for m in (context.conversation_history or [])
        ],
        "system": "\n\n".join(x for x in (getattr(context, "persona_context", "") or "", profile) if x),
    }
    status = "completed"
    started_at = datetime.now(timezone.utc)
    tool_names: list[str] = []
    wiki_pages: list[str] = []
    result_text: list[str] = []
    topic: str | None = None
    answer_parts: list[str] = []
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
                answer_parts.append(e.get("content", ""))
                yield StreamEvent(type=StreamEventType.CONTENT, source=_SRC, content=e.get("content", ""))
            elif kind == "tool_call":
                tool = e.get("tool", "")
                args = e.get("args") or {}
                tool_names.append(tool)
                if tool.endswith("llm_wiki__get") and isinstance(args.get("file"), str):
                    wiki_pages.append(args["file"])
                if tool.endswith("llm_wiki__query") and topic is None and isinstance(args.get("query"), str):
                    topic = args["query"][:80]
                yield StreamEvent(
                    type=StreamEventType.TOOL_CALL,
                    source=_SRC,
                    content=tool,
                    metadata={"args": args},
                )
            elif kind == "tool_result":
                result_text.append(str(e.get("content", ""))[:2000])
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
        await asyncio.to_thread(
            _record_turn_safely,
            session_id=context.session_id,
            question=question,
            answer="".join(answer_parts),
            tool_calls=tool_names,
            wiki_pages=wiki_pages,
            result_text=result_text,
            status=status,
            started_at=started_at,
            course_id=course_id,
            topic=topic,
        )
        if proc.returncode is None:
            proc.kill()
        await proc.wait()
        if not drain.done():
            drain.cancel()
    yield StreamEvent(type=StreamEventType.DONE, source=_SRC, metadata={"status": status})
