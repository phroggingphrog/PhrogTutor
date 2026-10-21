"""DeepTutor on the pi SDK (TASK-71), through one long-lived ``bun pi_engine/llm.ts --serve``.

* Agent turns: :func:`loop_turn` runs one DeepTutor agent turn in ``pi_engine/loop.ts`` (a pi agent
  session whose extension holds the round rules); ``deeptutor.agents.loop.pi_turn`` answers its asks
  (render a round, capability finish hooks, tool dispatch). pi plugins in ``pi_engine/agent-dir`` load.
* Single model calls: :func:`complete` / :func:`stream` (``services.llm.factory``: BaseAgent.call_llm /
  stream_llm) and :class:`PiClient` ``create()`` use pi's model layer (pi-ai ``stream``).

Agent turns always run on pi. ``PI_TUTOR_SDK=0`` only sends single model calls back to DeepTutor's own clients.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncGenerator, AsyncIterator
import json
import os
from pathlib import Path
from typing import Any
import uuid

_LLM_TS = Path(__file__).resolve().parents[3] / "pi_engine" / "llm.ts"
# The bun binary that runs pi_engine (name it e.g. "pi-engine" via a symlink so OOM killers can spare it).
_ENGINE_BIN = os.path.expanduser(os.environ.get("PI_TUTOR_ENGINE_BIN", "bun"))
_PASS = ("model", "messages", "tools", "temperature", "max_tokens", "max_completion_tokens", "reasoning_effort", "connection")


def pi_sdk_enabled() -> bool:
    return os.environ.get("PI_TUTOR_SDK", "1").lower() not in ("0", "false", "no")


_warned_unserved = False


async def _with_connection(request: dict[str, Any]) -> dict[str, Any]:
    """Attach the active catalog profile as the request's endpoint, when that mode is on and pi can serve it."""
    from .pi_connection import catalog_connections_enabled, codex_model_slug, is_codex, resolve_connection

    if request.get("connection") is not None or not catalog_connections_enabled():
        return request
    global _warned_unserved
    from .config import get_llm_config

    config = get_llm_config()
    connection = await resolve_connection(config)
    if connection is None and not _warned_unserved:
        _warned_unserved = True
        logging.getLogger(__name__).warning(
            "PI_TUTOR_LLM_FROM_CATALOG is on but the active profile cannot be served through pi "
            "(Copilot, CodeBuddy or Azure preview); using the gateway in pi_engine/agent-dir/models.json"
        )
    if not connection:
        return request
    request = {**request, "connection": connection}
    if is_codex(config) and request.get("model"):
        request["model"] = codex_model_slug(request["model"])
    return request


class _PiProcess:
    """One long-lived ``bun llm.ts --serve`` per event loop; requests share it, tagged by ``rid``."""

    def __init__(self) -> None:
        self.proc: asyncio.subprocess.Process | None = None
        self.queues: dict[str, asyncio.Queue] = {}
        self.lock = asyncio.Lock()

    async def _ensure(self) -> asyncio.subprocess.Process:
        async with self.lock:
            if self.proc is None or self.proc.returncode is not None:
                self.proc = await asyncio.create_subprocess_exec(
                    _ENGINE_BIN, str(_LLM_TS), "--serve",
                    stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=open(os.environ["PI_TUTOR_DEBUG_LOG"], "a") if os.environ.get("PI_TUTOR_DEBUG_LOG") else asyncio.subprocess.DEVNULL,
                    limit=16 * 1024 * 1024,
                )
                asyncio.create_task(self._read(self.proc))
            return self.proc

    async def _read(self, proc: asyncio.subprocess.Process) -> None:
        async for line in proc.stdout:
            if line.strip():
                msg = json.loads(line)
                queue = self.queues.get(msg.pop("rid", ""))
                if queue is not None:
                    queue.put_nowait(msg)
        for queue in self.queues.values():  # the process died: end every open request
            queue.put_nowait({"end": True, "error": "pi SDK process exited"})

    async def chunks(self, request: dict[str, Any]) -> AsyncIterator[dict[str, Any]]:
        rid = uuid.uuid4().hex
        queue: asyncio.Queue = asyncio.Queue()
        self.queues[rid] = queue
        try:
            proc = await self._ensure()
            request = await _with_connection(request)
            req = {k: request[k] for k in _PASS if request.get(k) is not None}
            proc.stdin.write((json.dumps({"rid": rid, "req": req}) + "\n").encode())
            await proc.stdin.drain()
            while not (msg := await queue.get()).get("end"):
                yield msg
            if msg.get("error"):
                raise RuntimeError(f"pi SDK model call failed: {msg['error'][-500:]}")
        finally:
            self.queues.pop(rid, None)


    async def _send(self, obj: dict[str, Any]) -> None:
        proc = await self._ensure()
        proc.stdin.write((json.dumps(obj) + "\n").encode())
        await proc.stdin.drain()

    async def loop_turn(self, request: dict[str, Any], cfg: dict[str, Any], host: Any) -> dict[str, Any]:
        """Run one agent turn in ``pi_engine/loop.ts``; answer its asks with ``host`` until it ends."""
        rid = uuid.uuid4().hex
        queue: asyncio.Queue = asyncio.Queue()
        self.queues[rid] = queue
        try:
            request = await _with_connection(request)
            req = {k: request[k] for k in _PASS if request.get(k) is not None}
            await self._send({"rid": rid, "kind": "loop", "req": req, "cfg": cfg})
            while True:
                msg = await queue.get()
                if msg.get("end"):
                    return msg.get("outcome") or {
                        "error": msg.get("error") or "pi agent turn ended", "error_kind": "transport", "partial": True,
                    }
                if "ask" in msg:
                    try:
                        data = await host.ask(msg.pop("ask"), msg)
                    except BaseException as exc:  # end the pi session too, then fail the turn here
                        await self._send({"rid": rid, "kind": "reply", "seq": msg["seq"], "data": {"_error": str(exc)}})
                        raise
                    await self._send({"rid": rid, "kind": "reply", "seq": msg["seq"], "data": data or {}})
                elif "notice" in msg:
                    await host.notice(msg["notice"], msg.get("vars") or {})
                elif msg.get("release"):
                    await host.release()
                else:
                    await host.chunk(msg)
        finally:
            self.queues.pop(rid, None)


_PROCS: dict[int, _PiProcess] = {}
def _proc() -> _PiProcess:
    return _PROCS.setdefault(id(asyncio.get_running_loop()), _PiProcess())


def _chunks(request: dict[str, Any]) -> AsyncIterator[dict[str, Any]]:
    return _proc().chunks(request)


async def loop_turn(request: dict[str, Any], cfg: dict[str, Any], host: Any) -> dict[str, Any]:
    return await _proc().loop_turn(request, cfg, host)


def _messages(prompt: str, system_prompt: str, messages: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    return messages or [{"role": "system", "content": system_prompt}, {"role": "user", "content": prompt}]


async def stream(
    prompt: str, system_prompt: str, model: str | None, messages: list[dict[str, Any]] | None,
    outcome: Any = None, **kwargs: Any,
) -> AsyncGenerator[str, None]:
    request = {"model": model, "messages": _messages(prompt, system_prompt, messages), **kwargs}
    async for chunk in _chunks(request):
        choice = (chunk.get("choices") or [{}])[0]
        text = (choice.get("delta") or {}).get("content")
        if text:
            yield text
        if choice.get("finish_reason") and outcome is not None:
            outcome.finish_reason = choice["finish_reason"]
            outcome.usage = chunk.get("usage") or {}


async def complete(
    prompt: str, system_prompt: str, model: str | None, messages: list[dict[str, Any]] | None, **kwargs: Any,
) -> str:
    return "".join([part async for part in stream(prompt, system_prompt, model, messages, **kwargs)])


class _Completions:
    async def create(self, **kwargs: Any) -> Any:
        from openai.types.chat import ChatCompletion, ChatCompletionChunk

        if kwargs.get("stream"):
            async def gen() -> AsyncIterator[ChatCompletionChunk]:
                async for chunk in _chunks(kwargs):
                    yield ChatCompletionChunk.model_validate(chunk)

            return gen()
        text, tool_calls, finish, usage, base = [], {}, "stop", None, None
        async for chunk in _chunks(kwargs):
            base = base or chunk
            choice = chunk["choices"][0]
            delta = choice.get("delta") or {}
            text.append(delta.get("content") or "")
            for tc in delta.get("tool_calls") or []:
                tool_calls[tc["index"]] = {k: tc[k] for k in ("id", "type", "function")}
            finish = choice.get("finish_reason") or finish
            usage = chunk.get("usage") or usage
        message: dict[str, Any] = {"role": "assistant", "content": "".join(text) or None}
        if tool_calls:
            message["tool_calls"] = [tool_calls[i] for i in sorted(tool_calls)]
        return ChatCompletion.model_validate({
            "id": (base or {}).get("id", "pi"), "object": "chat.completion", "created": (base or {}).get("created", 0),
            "model": (base or {}).get("model", kwargs.get("model") or ""),
            "choices": [{"index": 0, "message": message, "finish_reason": finish}], "usage": usage,
        })


class _Chat:
    completions = _Completions()


class PiClient:
    """Stands in for ``openai.AsyncOpenAI``: only ``chat.completions.create`` is used by the loop."""

    chat = _Chat()

    async def close(self) -> None:
        return None
