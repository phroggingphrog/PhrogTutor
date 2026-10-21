"""The agent loop's round rules (pi_engine/loop.ts) against a scripted model server, through the real pipeline."""

from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import shutil
import threading
import time
from pathlib import Path

import pytest

from deeptutor.agents.chat.agentic_pipeline import AgenticChatPipeline
from deeptutor.core.context import UnifiedContext
from deeptutor.core.stream import StreamEventType
from deeptutor.services.llm.config import LLMConfig
from deeptutor.services.llm import LLMProviderTransportError
from tests.agents.chat.loop_helpers import Registry, isolated_runtime as isolated_runtime, run_turn

ENGINE = Path(__file__).resolve().parents[3] / "pi_engine"
pytestmark = pytest.mark.skipif(
    shutil.which("bun") is None or not (ENGINE / "node_modules").exists(), reason="the pi engine is not installed"
)


def _sse(*chunks: dict) -> str:
    return "".join(f"data: {json.dumps(c)}\n\n" for c in chunks) + "data: [DONE]\n\n"


def _chunk(delta: dict, finish: str | None = None, **extra) -> dict:
    return {"id": "c", "object": "chat.completion.chunk", "model": "m", "choices": [{"index": 0, "delta": delta, "finish_reason": finish}], **extra}


USAGE = {"usage": {"prompt_tokens": 3, "completion_tokens": 4, "total_tokens": 7}}
TEXT = _sse(_chunk({"role": "assistant", "content": "The answer."}), _chunk({}, "stop", **USAGE))
TOOL = _sse(
    _chunk({"role": "assistant", "tool_calls": [{"index": 0, "id": "call1", "type": "function", "function": {"name": "web_search", "arguments": '{"query":"q"}'}}]}),
    _chunk({}, "tool_calls", **USAGE),
)


@pytest.fixture()
def model_server(monkeypatch):
    """A scripted OpenAI-compatible server: replies pop from `script` (a str, or a float to stall that many seconds)."""
    state = {"script": [], "seen": []}

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):  # noqa: N802
            body = json.loads(self.rfile.read(int(self.headers.get("content-length", 0))) or b"{}")
            state["seen"].append(body)
            reply = state["script"].pop(0) if state["script"] else TEXT
            if isinstance(reply, float):
                time.sleep(reply)
                return
            self.send_response(200)
            self.send_header("content-type", "text/event-stream")
            self.end_headers()
            self.wfile.write(reply.encode())

        def log_message(self, *_):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    config = LLMConfig(
        model="m", api_key="k", base_url=f"http://127.0.0.1:{server.server_address[1]}/v1", binding="custom", provider_name="custom"
    )
    monkeypatch.setattr("deeptutor.services.llm.config.get_llm_config", lambda: config)
    monkeypatch.setenv("PI_TUTOR_LLM_FROM_CATALOG", "1")
    monkeypatch.delenv("PI_TUTOR_LLM_BASE_URL", raising=False)
    yield state
    server.shutdown()


def _pipeline(monkeypatch, rounds: int = 1) -> AgenticChatPipeline:
    pipeline = AgenticChatPipeline(language="en")
    pipeline.registry = Registry()
    pipeline._max_rounds = rounds
    monkeypatch.setattr(pipeline, "_compose_enabled_tools", lambda _: ["web_search"])
    return pipeline


def _result(events):
    return [e for e in events if e.type == StreamEventType.RESULT][-1]


@pytest.mark.asyncio
async def test_request_only_messages_reach_the_model_but_not_the_transcript(model_server, monkeypatch) -> None:
    pipeline = _pipeline(monkeypatch)

    async def prepare(context, stream, messages):
        return [{"role": "user", "content": "CACHED-LESSON-PAGE"}]

    monkeypatch.setattr(pipeline, "prepare_model_request", prepare, raising=False)
    context = UnifiedContext(session_id="s", user_message="Teach me")
    events = await run_turn(pipeline, context)
    assert "CACHED-LESSON-PAGE" in json.dumps(model_server["seen"][0]["messages"])
    assert "CACHED-LESSON-PAGE" not in json.dumps(context.runtime.model_turn)
    assert _result(events).metadata["response"] == "The answer."


@pytest.mark.asyncio
async def test_a_capability_gets_one_restricted_round_after_settlement_then_the_turn_is_rejected(model_server, monkeypatch) -> None:
    pipeline = _pipeline(monkeypatch)
    monkeypatch.setattr(pipeline, "_capability_settlement_recovery", lambda context: ("Deliver the lesson now.", ("web_search",)))
    model_server["script"] = [TOOL] * 5  # one exploration round, three settlement rounds, the restricted round
    events = await run_turn(pipeline, UnifiedContext(session_id="s", user_message="Teach me"))
    seen = model_server["seen"]
    assert len(seen) == 5  # no sixth round, not even a tool-less salvage
    assert [t["function"]["name"] for t in seen[-1]["tools"]] == ["web_search"]
    assert "Deliver the lesson now." in json.dumps(seen[-1]["messages"])
    assert _result(events).metadata["completed"] is False


@pytest.mark.asyncio
async def test_a_provider_that_goes_silent_settles_instead_of_parking_the_turn(model_server, monkeypatch) -> None:
    monkeypatch.setenv("PI_TUTOR_IDLE_TIMEOUT_S", "1")
    pipeline = _pipeline(monkeypatch)
    model_server["script"] = [30.0, 30.0, 30.0]
    started = time.monotonic()
    with pytest.raises(LLMProviderTransportError):
        await run_turn(pipeline, UnifiedContext(session_id="s", user_message="Hello"))
    assert time.monotonic() - started < 60
    assert len(model_server["seen"]) == 3  # retried twice before any output, as for any transport failure


EMPTY = _sse(_chunk({"role": "assistant", "content": ""}), _chunk({}, "stop", **USAGE))


@pytest.mark.asyncio
async def test_a_loop_directive_extends_the_text_of_a_multimodal_user_message(model_server, monkeypatch) -> None:
    pipeline = _pipeline(monkeypatch, rounds=3)
    image = {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}}
    original = pipeline._build_loop_messages

    def with_parts(**kwargs):
        messages = original(**kwargs)
        messages[-1] = {"role": "user", "content": [{"type": "text", "text": "Describe the figure."}, image]}
        return messages

    monkeypatch.setattr(pipeline, "_build_loop_messages", with_parts)
    model_server["script"] = [EMPTY, TEXT]  # a round of nothing makes the loop add a nudge to the last user message
    await run_turn(pipeline, UnifiedContext(session_id="s", user_message="Describe the figure."))
    last_user = [m for m in model_server["seen"][1]["messages"] if m["role"] == "user"][-1]
    assert isinstance(last_user["content"], list)
    assert any(p.get("type") == "image_url" and p["image_url"]["url"].endswith("AAAA") for p in last_user["content"])
    text = " ".join(p.get("text", "") for p in last_user["content"] if p.get("type") == "text")
    assert text.startswith("Describe the figure.") and "reasoning" in text.lower()
    assert "base64" not in text  # image bytes never fold into prompt text


@pytest.mark.asyncio
async def test_a_context_checkpoint_keeps_only_the_latest_copy_of_its_state(model_server, monkeypatch) -> None:
    from deeptutor.core.tool_protocol import ToolResult

    class Checkpointing(Registry):
        async def execute(self, name, **kwargs):
            n = len(self.executed) + 1
            self.executed.append({"name": name, "kwargs": kwargs})
            return ToolResult(
                content="tool answer",
                metadata={"_context_checkpoint": {"summary": f"SUMMARY-{n}", "state": f"STATE-{n}"}},
                success=True,
            )

    pipeline = _pipeline(monkeypatch, rounds=5)
    pipeline.registry = Checkpointing()
    model_server["script"] = [TOOL, TOOL, TEXT]
    await run_turn(pipeline, UnifiedContext(session_id="s", user_message="Work it out"))
    second, third = (json.dumps(b["messages"]) for b in model_server["seen"][1:3])
    assert "SUMMARY-1" in second and "STATE-1" in second
    assert "SUMMARY-1" in third and "SUMMARY-2" in third  # history the fold keeps
    assert "STATE-2" in third and "STATE-1" not in third  # current state replaces the previous copy
