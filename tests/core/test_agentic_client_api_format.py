"""``api_format`` on the agentic OpenAI-compatible client handle."""

from __future__ import annotations

from deeptutor.runtime.agentic import client as agentic_client
from deeptutor.runtime.agentic.client import LLMClientConfig, can_use_native_tool_calling
from deeptutor.services.llm.capabilities import set_catalog_capability_overrides


def test_api_format_and_wire_api_stay_in_step() -> None:
    assert (
        LLMClientConfig(
            binding="custom", model="m", api_key="k", base_url=None, api_format="openai_responses"
        ).wire_api
        == "responses"
    )
    assert (
        LLMClientConfig(
            binding="custom", model="m", api_key="k", base_url=None, wire_api="responses"
        ).api_format
        == "openai_responses"
    )
    assert (
        LLMClientConfig(binding="anthropic", model="m", api_key="k", base_url=None).api_format
        == "anthropic"
    )
    # Clamped: OpenAI does not serve Anthropic Messages.
    clamped = LLMClientConfig(
        binding="openai", model="m", api_key="k", base_url=None, api_format="anthropic"
    )
    assert (clamped.api_format, clamped.wire_api) == ("auto", "auto")


def test_declared_tool_capability_decides_before_every_other_rule() -> None:
    try:
        set_catalog_capability_overrides(
            [
                ("ollama", "llama3", {"tools": True}),
                ("openai", "gpt-4o", {"tools": False}),
            ]
        )
        assert can_use_native_tool_calling(binding="ollama", model="llama3") is True
        assert can_use_native_tool_calling(binding="openai", model="gpt-4o") is False
        assert can_use_native_tool_calling(binding="openai", model="gpt-4o-mini") is True
    finally:
        set_catalog_capability_overrides([])
