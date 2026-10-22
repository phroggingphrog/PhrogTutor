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
    replies = {"/v1/chat/completions": OPENAI_CHAT, "/v1/messages": ANTHROPIC, "/v1/responses": RESPONSES, "/openai/v1/responses": RESPONSES, "/codex/responses": RESPONSES}

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):  # noqa: N802
            body = self.rfile.read(int(self.headers.get("content-length", 0)))
            try:
                parsed = json.loads(body or b"{}")
            except ValueError:  # the Codex backend body may be compressed
                parsed = {}
            seen.append({"path": self.path, "headers": {k.lower(): v for k, v in self.headers.items()}, "body": parsed})
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


def _ask(connection: dict, **extra) -> str:
    request = {"model": "m", "messages": [{"role": "user", "content": "hi"}], "connection": connection, **extra}
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


def _jwt(account: str) -> str:
    import base64

    part = lambda obj: base64.urlsafe_b64encode(json.dumps(obj).encode()).decode().rstrip("=")  # noqa: E731
    return f"{part({'alg': 'none'})}.{part({'https://api.openai.com/auth': {'chatgpt_account_id': account}})}.sig"


@needs_engine
def test_engine_reaches_the_codex_backend_with_the_signed_in_account(endpoint) -> None:
    url, seen = endpoint
    token = _jwt("acct-42")
    text = _ask({"api": "openai-codex-responses", "provider": "openai-codex", "baseUrl": url, "apiKey": token, "headers": None, "transport": "sse"})
    assert text == "hello from responses"
    request = seen[0]
    assert request["path"] == "/codex/responses"
    assert request["headers"]["authorization"] == f"Bearer {token}"
    assert request["headers"]["chatgpt-account-id"] == "acct-42"


def test_codex_profile_takes_its_token_from_the_codex_service(monkeypatch) -> None:
    import asyncio
    from types import SimpleNamespace

    from deeptutor.services.llm import pi_connection as module

    calls = []
    service = SimpleNamespace(
        get_token=lambda: _async(SimpleNamespace(access_token="tok", account_id="a", generation=1)),
        validate_runtime_profile=lambda token, model, effort: calls.append((token.access_token, model, effort)),
    )
    monkeypatch.setattr("deeptutor.services.codex_auth.get_codex_oauth_service", lambda: service)
    config = _config(binding="openai_codex", provider_name="openai_codex", model="openai-codex/gpt-5.5", base_url=None)
    conn = asyncio.run(module.resolve_connection(config))
    assert conn["api"] == "openai-codex-responses" and conn["apiKey"] == "tok" and conn["transport"] == "sse"
    assert calls == [("tok", "gpt-5.5", None)]


async def _async(value):
    return value


KEY = "a" * 64


@needs_engine
@pytest.mark.parametrize(
    "api,path,base",
    [("openai-completions", "/v1/chat/completions", "/v1"), ("openai-responses", "/v1/responses", "/v1")],
)
def test_openai_style_gateways_get_the_cache_key_that_keeps_a_prefix_on_one_cache(endpoint, api, path, base) -> None:
    url, seen = endpoint
    _ask({"api": api, "provider": "custom", "baseUrl": f"{url}{base}", "apiKey": "k", "headers": None}, cache_key=KEY)
    assert seen[0]["path"] == path
    assert seen[0]["body"]["prompt_cache_key"] == KEY


@needs_engine
def test_other_vendors_do_not_receive_an_openai_cache_key(endpoint) -> None:
    url, seen = endpoint
    _ask({"api": "anthropic-messages", "provider": "anthropic", "baseUrl": url, "apiKey": "k", "headers": None}, cache_key=KEY)
    assert "prompt_cache_key" not in seen[0]["body"]
    # pi's own Anthropic caching stays on: the system prompt carries an ephemeral breakpoint
    assert "cache_control" in json.dumps(seen[0]["body"])


def test_cache_key_follows_the_system_prefix_not_the_history() -> None:
    from deeptutor.services.llm.pi_bridge import cache_key

    system = [{"role": "system", "content": "rules"}]
    first = [*system, {"role": "user", "content": "q"}]
    later = [*first, {"role": "assistant", "content": "a"}, {"role": "user", "content": "next"}]
    assert cache_key(first) == cache_key(later) != cache_key([{"role": "system", "content": "other rules"}])
    assert cache_key([{"role": "user", "content": "no system"}]) is None


@needs_engine
def test_agent_loop_requests_carry_the_same_cache_key(endpoint, monkeypatch) -> None:
    import asyncio

    from deeptutor.services.llm import pi_bridge

    url, seen = endpoint

    class Host:
        async def ask(self, kind, data=None):
            return {}

        async def notice(self, *_):
            pass

        async def release(self):
            pass

        async def chunk(self, _):
            pass

    request = {
        "model": "m",
        "messages": [{"role": "system", "content": "rules"}, {"role": "user", "content": "hi"}],
        "connection": {"api": "openai-completions", "provider": "custom", "baseUrl": f"{url}/v1", "apiKey": "k", "headers": None},
    }
    asyncio.run(pi_bridge.loop_turn(request, {"budget": 1, "texts": {}}, Host()))  # the stub host ends the turn; only the request matters
    assert seen and seen[0]["body"]["prompt_cache_key"] == pi_bridge.cache_key(request["messages"])


@needs_engine
def test_azure_preview_api_version_reaches_the_url_without_leaking_the_marker_header(endpoint) -> None:
    url, seen = endpoint
    connection = pi_connection(
        _config(binding="azure_openai", provider_name="azure_openai", base_url=url, api_key="az-secret", api_version="preview")
    )
    assert connection["headers"]["x-pi-query"] == "api-version=preview"
    assert _ask(connection) == "hello from responses"
    assert seen[0]["path"] == "/openai/v1/responses?api-version=preview"
    assert "x-pi-query" not in seen[0]["headers"]


@needs_engine
def test_the_engine_gives_its_memory_back_when_idle_and_returns_on_demand(endpoint, monkeypatch) -> None:
    import asyncio

    from deeptutor.services.llm import pi_bridge

    url, _seen = endpoint
    monkeypatch.setenv("PI_TUTOR_ENGINE_IDLE_S", "2")
    monkeypatch.setenv("PI_TUTOR_LLM_FROM_CATALOG", "1")
    monkeypatch.delenv("PI_TUTOR_LLM_BASE_URL", raising=False)
    monkeypatch.setattr(
        "deeptutor.services.llm.config.get_llm_config",
        lambda: _config(binding="custom", provider_name="custom", base_url=f"{url}/v1"),
    )

    async def scenario() -> tuple[bool, bool, str]:
        process = pi_bridge._PiProcess()
        first = await process._ensure()
        async for _ in process._chunks_once({"model": "m", "messages": [{"role": "user", "content": "hi"}], "connection": {"api": "openai-completions", "baseUrl": f"{url}/v1", "apiKey": "k", "headers": None}}):
            pass
        await asyncio.sleep(5)
        gone = first.returncode is not None and process.proc is None
        text = ""
        async for chunk in process.chunks({"model": "m", "messages": [{"role": "user", "content": "hi"}]}):
            text += (chunk["choices"][0]["delta"].get("content") or "") if chunk.get("choices") else ""
        alive = process.proc is not None and process.proc.returncode is None
        process.proc.terminate()
        return gone, alive, text

    gone, alive, text = asyncio.run(scenario())
    assert gone and alive and text == "hello from openai"
