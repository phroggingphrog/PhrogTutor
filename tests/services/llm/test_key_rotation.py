"""A profile that lists several API keys moves to the next key when one is rate limited."""

from __future__ import annotations

import pytest

from deeptutor.services.llm import pi_bridge, pi_connection
from deeptutor.services.llm.config import LLMConfig


def _config() -> LLMConfig:
    return LLMConfig(
        model="m",
        api_key=["key-a", "key-b", "key-c"],
        base_url="https://api.example.test/v1",
        binding="openai",
        provider_name="openai",
    )


def test_each_request_takes_the_next_key_of_the_pool() -> None:
    config = _config()
    seen = [pi_connection.pi_connection(config)["apiKey"] for _ in range(4)]
    assert seen == ["key-a", "key-b", "key-c", "key-a"]


@pytest.mark.asyncio
async def test_llm_call_tries_every_api_key_after_429(monkeypatch: pytest.MonkeyPatch) -> None:
    config = _config()
    monkeypatch.setattr("deeptutor.services.llm.config.get_llm_config", lambda: config)
    monkeypatch.setattr(pi_connection, "_POOLS", {})
    monkeypatch.delenv("PI_TUTOR_LLM_BASE_URL", raising=False)
    monkeypatch.setenv("PI_TUTOR_LLM_FROM_CATALOG", "1")
    used: list[str] = []

    async def once(self, request):
        used.append(request["connection"]["apiKey"])
        if len(used) < 3:
            raise RuntimeError("pi SDK model call failed: 429 rate limited")
        yield {"choices": [{"delta": {"content": "ok"}}]}

    monkeypatch.setattr(pi_bridge._PiProcess, "_chunks_once", once)
    chunks = [c async for c in pi_bridge._PiProcess().chunks({"model": "m", "messages": []})]
    assert chunks == [{"choices": [{"delta": {"content": "ok"}}]}]
    assert used == ["key-a", "key-b", "key-c"]


@pytest.mark.asyncio
async def test_a_single_key_profile_does_not_retry(monkeypatch: pytest.MonkeyPatch) -> None:
    config = _config().model_copy(update={"api_key": "only"})
    monkeypatch.setattr("deeptutor.services.llm.config.get_llm_config", lambda: config)
    monkeypatch.setenv("PI_TUTOR_LLM_FROM_CATALOG", "1")
    calls = 0

    async def once(self, request):
        nonlocal calls
        calls += 1
        raise RuntimeError("pi SDK model call failed: 429")
        yield  # pragma: no cover

    monkeypatch.setattr(pi_bridge._PiProcess, "_chunks_once", once)
    with pytest.raises(RuntimeError):
        [c async for c in pi_bridge._PiProcess().chunks({"model": "m", "messages": []})]
    assert calls == 1


def test_a_real_429_from_the_first_key_moves_the_call_to_the_next_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """Through the real engine: it opens each call with an empty role chunk, which is not output."""
    import asyncio
    import json
    import shutil
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from pathlib import Path

    engine = Path(__file__).resolve().parents[3] / "pi_engine"
    if shutil.which("bun") is None or not (engine / "node_modules").exists():
        pytest.skip("the pi engine is not installed")
    seen: list[str] = []
    ok = (
        'data: {"id":"c","object":"chat.completion.chunk","model":"m","choices":[{"index":0,"delta":{"role":"assistant","content":"served"},"finish_reason":null}]}\n\n'
        'data: {"id":"c","object":"chat.completion.chunk","model":"m","choices":[{"index":0,"delta":{},"finish_reason":"stop"}]}\n\n'
        "data: [DONE]\n\n"
    )

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):  # noqa: N802
            self.rfile.read(int(self.headers.get("content-length", 0)))
            key = self.headers.get("authorization", "").removeprefix("Bearer ")
            seen.append(key)
            if key == "key-a":
                body = json.dumps({"error": {"message": "rate limited", "type": "rate_limit"}}).encode()
                self.send_response(429)
                self.send_header("content-type", "application/json")
                self.send_header("content-length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            self.send_response(200)
            self.send_header("content-type", "text/event-stream")
            self.end_headers()
            self.wfile.write(ok.encode())

        def log_message(self, *_):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    config = LLMConfig(
        model="m",
        api_key=["key-a", "key-b"],
        base_url=f"http://127.0.0.1:{server.server_address[1]}/v1",
        binding="custom",
        provider_name="custom",
    )
    monkeypatch.setattr("deeptutor.services.llm.config.get_llm_config", lambda: config)
    monkeypatch.setattr(pi_connection, "_POOLS", {})
    monkeypatch.setenv("PI_TUTOR_LLM_FROM_CATALOG", "1")
    monkeypatch.delenv("PI_TUTOR_LLM_BASE_URL", raising=False)
    try:
        text = asyncio.run(pi_bridge.complete("hi", "sys", "m", None))
    finally:
        server.shutdown()
    assert text == "served"
    assert seen[0] == "key-a" and seen[-1] == "key-b"
