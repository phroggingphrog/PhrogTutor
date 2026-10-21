from __future__ import annotations

from types import SimpleNamespace

import pytest

from deeptutor.runtime.agentic.client import (
    build_completion_kwargs,
    can_use_native_tool_calling,
)


def test_agentic_kwargs_leave_deepseek_flash_thinking_to_the_provider() -> None:
    """Flash is no longer switched off — see ``_THINKING_DISABLED_BY_DEFAULT_MODELS``."""
    kwargs = build_completion_kwargs(
        temperature=0.7,
        model="deepseek-v4-flash",
        max_tokens=1024,
        binding="deepseek",
    )

    assert kwargs["max_tokens"] == 1024
    assert "reasoning_effort" not in kwargs
    assert "extra_body" not in kwargs


def test_agentic_kwargs_enable_deepseek_pro_thinking_by_default() -> None:
    kwargs = build_completion_kwargs(
        temperature=0.7,
        model="deepseek-v4-pro",
        max_tokens=1024,
        binding="deepseek",
    )

    assert kwargs["reasoning_effort"] == "high"
    assert kwargs["extra_body"] == {"thinking": {"type": "enabled"}}


def test_agentic_kwargs_use_provider_minimal_thinking_without_top_level_effort() -> None:
    kwargs = build_completion_kwargs(
        temperature=0.7,
        model="deepseek-v4-pro",
        max_tokens=1024,
        binding="deepseek",
        reasoning_effort="minimal",
    )

    assert "reasoning_effort" not in kwargs
    assert kwargs["extra_body"] == {"thinking": {"type": "disabled"}}


def test_agentic_kwargs_enable_qwen_thinking_for_custom_binding() -> None:
    kwargs = build_completion_kwargs(
        temperature=0.7,
        model="qwen3.6-plus",
        max_tokens=1024,
        binding="custom",
    )

    assert kwargs["max_tokens"] == 1024
    assert "reasoning_effort" not in kwargs
    assert kwargs["extra_body"] == {"enable_thinking": True}


def test_agentic_kwargs_disable_qwen_thinking_for_custom_minimal_reasoning() -> None:
    kwargs = build_completion_kwargs(
        temperature=0.7,
        model="Qwen/Qwen3-235B-A22B-Instruct",
        max_tokens=1024,
        binding="custom",
        reasoning_effort="minimal",
    )

    assert "reasoning_effort" not in kwargs
    assert kwargs["extra_body"] == {"enable_thinking": False}


def test_agentic_kwargs_preserve_legacy_shape_without_binding() -> None:
    kwargs = build_completion_kwargs(
        temperature=0.2,
        model="plain-model",
        max_tokens=256,
    )

    assert kwargs == {"temperature": 0.2, "max_tokens": 256}


@pytest.mark.parametrize("binding", ["moonshot", "openai", "custom"])
def test_agentic_kwargs_drop_temperature_for_bare_kimi_k3(binding: str) -> None:
    kwargs = build_completion_kwargs(
        temperature=0.2,
        model="k3",
        max_tokens=256,
        binding=binding,
    )

    assert kwargs == {"max_tokens": 256}


@pytest.mark.parametrize("model", ["sk3", "k30", "vendor/k3"])
def test_short_k3_override_does_not_match_unrelated_models(model: str) -> None:
    kwargs = build_completion_kwargs(
        temperature=0.2,
        model=model,
        max_tokens=256,
        binding="moonshot",
    )

    assert kwargs["temperature"] == pytest.approx(0.2)


def test_anthropic_backend_can_use_native_tool_calling() -> None:
    assert can_use_native_tool_calling(binding="custom_anthropic", model="claude-test") is True
    assert can_use_native_tool_calling(binding="minimax_anthropic", model="MiniMax-M3") is True


def test_custom_qwen_can_use_native_tool_calling() -> None:
    assert can_use_native_tool_calling(binding="custom", model="qwen3.6-plus") is True
    assert can_use_native_tool_calling(binding="dashscope", model="qwen-plus") is True


def test_siliconflow_deepseek_can_use_native_tool_calling() -> None:
    assert (
        can_use_native_tool_calling(
            binding="siliconflow",
            model="deepseek-ai/DeepSeek-V4-Pro",
        )
        is True
    )


def test_registered_cloud_openai_compat_providers_enable_native_tools() -> None:
    # Registered cloud OpenAI-compatible providers are tool-capable by default,
    # even without a dedicated PROVIDER_CAPABILITIES entry — function calling is
    # part of the OpenAI-compatible API contract. Guards against silently
    # disabling native tools when a new cloud provider joins the registry (the
    # gap that affected SiliconFlow before #584).
    for binding in (
        "gemini",
        "zhipu",
        "qianfan",
        "stepfun",
        "xiaomi_mimo",
        "nvidia_nim",
        "aihubmix",
        "atlascloud",
        "unifically",
        "cheaperinference",
        "api_route",
        "requesty",
        "futureinfra",
        "edenai",
        "novita",
        "volcengine_coding_plan",
        "byteplus_coding_plan",
    ):
        assert can_use_native_tool_calling(binding=binding, model=None) is True, binding


def test_openai_codex_backend_can_use_native_tool_calling() -> None:
    assert (
        can_use_native_tool_calling(
            binding="openai_codex",
            model="openai-codex/gpt-5.5",
        )
        is True
    )


def test_local_backends_stay_opted_out_of_native_tools() -> None:
    # Local OpenAI-compatible servers have model-dependent, unreliable tool support.
    for binding in (
        "ollama",
        "vllm",
        "lm_studio",
        "llama_cpp",
        "lemonade",
        "ovms",
    ):
        assert can_use_native_tool_calling(binding=binding, model=None) is False, binding


def test_unknown_binding_does_not_enable_native_tools() -> None:
    assert can_use_native_tool_calling(binding="totally-unknown", model=None) is False
