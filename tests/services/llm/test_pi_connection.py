"""A catalog profile reaches the right endpoint, with the right protocol and credentials, through pi."""

from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import shutil
import subprocess
import threading

import pytest

from deeptutor.services.llm.config import LLMConfig
from deeptutor.services.llm.pi_connection import catalog_connections_enabled, pi_connection

ENGINE = Path(__file__).resolve().parents[3] / "pi_engine"


def _config(**kw) -> LLMConfig:
    base = dict(model="m", api_key="k", base_url="https://api.openai.com/v1", binding="openai", provider_name="openai")
    return LLMConfig(**{**base, **kw})


def test_openai_compatible_profile_uses_chat_completions() -> None:
    assert pi_connection(_config()) == {
        "api": "openai-completions",
        "provider": "openai",
        "baseUrl": "https://api.openai.com/v1",
        "apiKey": "k",
        "headers": None,
    }


def test_responses_wire_api_and_headers_carry_over() -> None:
    conn = pi_connection(_config(wire_api="responses", extra_headers={"x-session": "s"}))
    assert conn["api"] == "openai-responses" and conn["headers"] == {"x-session": "s"}


def test_anthropic_profile_drops_the_v1_the_sdk_adds_back() -> None:
    conn = pi_connection(
        _config(binding="anthropic", provider_name="anthropic", base_url="https://api.anthropic.com/v1")
    )
    assert conn["api"] == "anthropic-messages" and conn["baseUrl"] == "https://api.anthropic.com"


def test_profiles_pi_cannot_serve_yet_fall_back() -> None:
    assert pi_connection(_config(binding="openai_codex", provider_name="openai_codex", base_url=None)) is None
    assert pi_connection(_config(binding="azure_openai", provider_name="azure_openai", base_url="https://r.openai.azure.com", api_version="preview")) is None


@pytest.mark.parametrize("base", [
    "https://r.openai.azure.com",
    "r.openai.azure.com/",
    "https://r.openai.azure.com/openai/v1/responses",
    "https://r.openai.azure.com/openai/deployments/gpt5/chat/completions?api-version=2024-06-01",
])
def test_azure_profile_speaks_responses_on_the_v1_surface(base) -> None:
    conn = pi_connection(_config(binding="azure_openai", provider_name="azure_openai", base_url=base, api_version="2024-06-01"))
    assert conn == {
        "api": "openai-responses",
        "provider": "azure_openai",
        "baseUrl": "https://r.openai.azure.com/openai/v1",
        "apiKey": "k",
        "headers": {"api-key": "k"},
    }


def test_the_catalog_is_used_by_default_only_when_no_gateway_is_configured(monkeypatch, tmp_path) -> None:
    from deeptutor.services.llm import pi_connection as module

    monkeypatch.delenv("PI_TUTOR_LLM_FROM_CATALOG", raising=False)
    monkeypatch.delenv("PI_TUTOR_LLM_BASE_URL", raising=False)
    monkeypatch.setattr(module, "_GATEWAY_FILE", tmp_path / "models.json")
    assert catalog_connections_enabled()  # fresh install: nothing else to use

    (tmp_path / "models.json").write_text("{}")
    assert not catalog_connections_enabled()  # an existing gateway file keeps working untouched

    (tmp_path / "models.json").unlink()
    monkeypatch.setenv("PI_TUTOR_LLM_BASE_URL", "http://gateway.test/v1")
    assert not catalog_connections_enabled()  # so does the environment variable

    monkeypatch.setenv("PI_TUTOR_LLM_FROM_CATALOG", "1")
    assert catalog_connections_enabled()  # and the switch overrides both
    monkeypatch.setenv("PI_TUTOR_LLM_FROM_CATALOG", "0")
    monkeypatch.delenv("PI_TUTOR_LLM_BASE_URL")
    assert not catalog_connections_enabled()


# ---- the engine against mock endpoints ----------------------------------------------------------

OPENAI_CHAT = (
    'data: {"id":"c1","object":"chat.completion.chunk","model":"m","choices":[{"index":0,"delta":{"role":"assistant","content":"hello from openai"},"finish_reason":null}]}\n\n'
    'data: {"id":"c1","object":"chat.completion.chunk","model":"m","choices":[{"index":0,"delta":{},"finish_reason":"stop"}],"usage":{"prompt_tokens":3,"completion_tokens":4,"total_tokens":7}}\n\n'
    "data: [DONE]\n\n"
)
ANTHROPIC = "".join(
    f"event: {name}\ndata: {json.dumps(data)}\n\n"
    for name, data in [
        ("message_start", {"type": "message_start", "message": {"id": "msg_1", "type": "message", "role": "assistant", "model": "m", "content": [], "stop_reason": None, "usage": {"input_tokens": 3, "output_tokens": 0}}}),
        ("content_block_start", {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}}),
        ("content_block_delta", {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "hello from anthropic"}}),
        ("content_block_stop", {"type": "content_block_stop", "index": 0}),
        ("message_delta", {"type": "message_delta", "delta": {"stop_reason": "end_turn"}, "usage": {"output_tokens": 4}}),
        ("message_stop", {"type": "message_stop"}),
    ]
)
RESPONSES = "".join(
    f"data: {json.dumps(data)}\n\n"
    for data in [
        {"type": "response.created", "response": {"id": "r1", "status": "in_progress"}},
        {"type": "response.output_item.added", "output_index": 0, "item": {"type": "message", "id": "m1", "role": "assistant", "status": "in_progress", "content": []}},
        {"type": "response.content_part.added", "item_id": "m1", "output_index": 0, "content_index": 0, "part": {"type": "output_text", "text": "", "annotations": []}},
        {"type": "response.output_text.delta", "item_id": "m1", "output_index": 0, "content_index": 0, "delta": "hello from responses"},
        {"type": "response.output_text.done", "item_id": "m1", "output_index": 0, "content_index": 0, "text": "hello from responses"},
        {"type": "response.content_part.done", "item_id": "m1", "output_index": 0, "content_index": 0, "part": {"type": "output_text", "text": "hello from responses", "annotations": []}},
        {"type": "response.output_item.done", "output_index": 0, "item": {"type": "message", "id": "m1", "role": "assistant", "status": "completed", "content": [{"type": "output_text", "text": "hello from responses", "annotations": []}]}},
        {"type": "response.completed", "response": {"id": "r1", "status": "completed", "usage": {"input_tokens": 3, "output_tokens": 4, "total_tokens": 7, "input_tokens_details": {"cached_tokens": 0}}}},
    ]
)


@pytest.fixture()
def endpoint():
    seen: list[dict] = []
    replies = {"/v1/chat/completions": OPENAI_CHAT, "/v1/messages": ANTHROPIC, "/v1/responses": RESPONSES, "/openai/v1/responses": RESPONSES}

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):  # noqa: N802
            body = self.rfile.read(int(self.headers.get("content-length", 0)))
            seen.append({"path": self.path, "headers": {k.lower(): v for k, v in self.headers.items()}, "body": json.loads(body or b"{}")})
            payload = replies.get(self.path.split("?")[0])
            if payload is None:
                self.send_response(404)
                self.end_headers()
                return
            self.send_response(200)
            self.send_header("content-type", "text/event-stream")
            self.end_headers()
            self.wfile.write(payload.encode())

        def log_message(self, *_):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}", seen
    server.shutdown()


def _ask(connection: dict) -> str:
    request = {"model": "m", "messages": [{"role": "user", "content": "hi"}], "connection": connection}
    done = subprocess.run(
        ["bun", str(ENGINE / "llm.ts")],
        input=json.dumps(request), capture_output=True, text=True, timeout=60, cwd=ENGINE,
    )
    assert done.returncode == 0, done.stderr[-800:]
    chunks = [json.loads(line) for line in done.stdout.splitlines() if line.strip()]
    return "".join((c["choices"][0]["delta"].get("content") or "") for c in chunks)


needs_engine = pytest.mark.skipif(
    shutil.which("bun") is None or not (ENGINE / "node_modules").exists(),
    reason="the pi engine is not installed",
)


@needs_engine
def test_engine_speaks_openai_chat_to_the_profile_endpoint(endpoint) -> None:
    url, seen = endpoint
    text = _ask({"api": "openai-completions", "provider": "custom", "baseUrl": f"{url}/v1", "apiKey": "sk-test", "headers": {"x-route": "go"}})
    assert text == "hello from openai"
    request = seen[0]
    assert request["path"] == "/v1/chat/completions"
    assert request["headers"]["authorization"] == "Bearer sk-test" and request["headers"]["x-route"] == "go"
    assert request["body"]["model"] == "m"


@needs_engine
def test_engine_speaks_anthropic_messages_with_its_own_auth_header(endpoint) -> None:
    url, seen = endpoint
    text = _ask({"api": "anthropic-messages", "provider": "anthropic", "baseUrl": url, "apiKey": "sk-ant-test", "headers": None})
    assert text == "hello from anthropic"
    request = seen[0]
    assert request["path"].split("?")[0] == "/v1/messages"
    assert request["headers"].get("x-api-key") == "sk-ant-test" and "anthropic-version" in request["headers"]


@needs_engine
def test_engine_speaks_openai_responses(endpoint) -> None:
    url, seen = endpoint
    text = _ask({"api": "openai-responses", "provider": "openai", "baseUrl": f"{url}/v1", "apiKey": "sk-test", "headers": None})
    assert text == "hello from responses"
    assert seen[0]["path"] == "/v1/responses"


@needs_engine
def test_engine_refuses_values_that_models_json_would_run(endpoint) -> None:
    url, _ = endpoint
    request = {"model": "m", "messages": [{"role": "user", "content": "hi"}],
               "connection": {"api": "openai-completions", "baseUrl": f"{url}/v1", "apiKey": "!echo pwned", "headers": None}}
    done = subprocess.run(["bun", str(ENGINE / "llm.ts")], input=json.dumps(request), capture_output=True, text=True, timeout=60, cwd=ENGINE)
    assert done.returncode != 0 and "may not start with" in (done.stderr + done.stdout)


@needs_engine
def test_engine_reaches_an_azure_deployment_with_the_api_key_header(endpoint) -> None:
    url, seen = endpoint
    connection = pi_connection(_config(binding="azure_openai", provider_name="azure_openai", base_url=url, api_key="az-secret"))
    assert _ask(connection) == "hello from responses"
    request = seen[0]
    assert request["path"] == "/openai/v1/responses"
    assert request["headers"]["api-key"] == "az-secret"
    assert request["body"]["model"] == "m"  # the model name is the deployment
