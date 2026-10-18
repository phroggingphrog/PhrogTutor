"""pi SDK as DeepTutor's model layer (TASK-71).

Every model call goes through ``pi_engine/llm.ts`` (the pi SDK's ``stream``), which reads an OpenAI
chat-completions request on stdin and prints OpenAI ``chat.completion.chunk`` lines. Two seams use it:

* :func:`complete` / :func:`stream`: called from ``services.llm.factory`` (BaseAgent.call_llm /
  stream_llm and every other helper that goes through the factory).
* :class:`PiClient`: returned by ``runtime.agentic.client.build_openai_client``, so the agent loop
  (chat, research, question pipelines) calls ``client.chat.completions.create`` unchanged.

On by default; ``PI_TUTOR_SDK=0`` sends everything back to DeepTutor's own OpenAI clients.
"""

from __future__ import annotations

import asyncio
import json
import os
import uuid
from collections.abc import AsyncGenerator, AsyncIterator
from pathlib import Path
from typing import Any

_LLM_TS = Path(__file__).resolve().parents[3] / "pi_engine" / "llm.ts"
_PASS = ("model", "messages", "tools", "temperature", "max_tokens", "max_completion_tokens", "reasoning_effort")


def pi_sdk_enabled() -> bool:
    return os.environ.get("PI_TUTOR_SDK", "1").lower() not in ("0", "false", "no")


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
                    "bun", str(_LLM_TS), "--serve",
                    stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
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
            req = {k: request[k] for k in _PASS if request.get(k) is not None}
            proc.stdin.write((json.dumps({"rid": rid, "req": req}) + "\n").encode())
            await proc.stdin.drain()
            while not (msg := await queue.get()).get("end"):
                yield msg
            if msg.get("error"):
                raise RuntimeError(f"pi SDK model call failed: {msg['error'][-500:]}")
        finally:
            self.queues.pop(rid, None)


_PROCS: dict[int, _PiProcess] = {}


def _chunks(request: dict[str, Any]) -> AsyncIterator[dict[str, Any]]:
    loop_id = id(asyncio.get_running_loop())
    return _PROCS.setdefault(loop_id, _PiProcess()).chunks(request)


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
