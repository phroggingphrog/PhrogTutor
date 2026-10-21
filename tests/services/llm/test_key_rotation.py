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
