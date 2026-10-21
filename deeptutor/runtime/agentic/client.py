"""LLM client handle and completion kwargs for the agent loops.

Every capability that wants a streaming LLM call with tools builds the same client + kwargs here. The client is
the pi engine (``services.llm.pi_bridge.PiClient``), which speaks to the active profile, so provider gating, Azure
detection, SSL handling and wire formats live there and not in each pipeline.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from deeptutor.services.llm import get_token_limit_kwargs, supports_tools
from deeptutor.services.llm.capabilities import catalog_capability_override
from deeptutor.services.llm.reasoning_params import (
    build_openai_compatible_reasoning_kwargs,
)
from deeptutor.services.provider_registry import (
    api_format_for_provider,
    api_format_from_legacy,
    effective_backend,
    find_by_name,
    model_overrides_for,
    normalize_api_format,
    wire_api_for_provider,
    wire_api_from_api_format,
)

# Providers that don't reliably support OpenAI function-calling. The loop
# still runs without tool schemas — the model just produces prose.
_NATIVE_TOOL_BLOCKED_BINDINGS: frozenset[str] = frozenset(
    {"anthropic", "claude", "ollama", "lm_studio", "vllm", "llama_cpp"}
)

# Native provider adapters whose backends speak OpenAI-style function calling
# end to end (schema serialization + tool-call parsing). Backends validated
# here get tools attached regardless of the binding blocklist above.
_NATIVE_TOOL_BACKENDS: frozenset[str] = frozenset({"anthropic", "openai_codex"})
@dataclass(frozen=True)
class LLMClientConfig:
    """Provider-neutral handle for constructing an OpenAI-compatible client."""

    binding: str
    model: str | None
    api_key: str | list[str] | None
    base_url: str | None
    api_version: str | None = None
    extra_headers: dict[str, str] | None = None
    reasoning_effort: str | None = None
    wire_api: str = "auto"
    api_format: str = "auto"

    def __post_init__(self) -> None:
        # Same rule as LLMConfig: an explicit ``api_format`` decides
        # ``wire_api``; a caller that only knows ``wire_api`` gets the format
        # derived from it. The two fields never disagree.
        spec = find_by_name(self.binding)
        if normalize_api_format(self.api_format) == "auto":
            object.__setattr__(self, "api_format", api_format_from_legacy(spec, self.wire_api))
            object.__setattr__(self, "wire_api", wire_api_for_provider(self.wire_api, spec))
        else:
            api_format = api_format_for_provider(self.api_format, spec)
            object.__setattr__(self, "api_format", api_format)
            object.__setattr__(
                self,
                "wire_api",
                wire_api_for_provider(wire_api_from_api_format(api_format), spec),
            )


def build_openai_client(config: LLMClientConfig) -> Any:
    """The metered client for one turn's model calls: the pi engine, against the active profile."""
    from deeptutor.services.llm.metrics import instrument_client
    from deeptutor.services.llm.pi_bridge import PiClient

    return instrument_client(PiClient(), model=config.model or "", provider=config.binding)


def build_completion_kwargs(
    *,
    temperature: float,
    model: str | None,
    max_tokens: int,
    binding: str | None = None,
    reasoning_effort: str | None = None,
) -> dict[str, Any]:
    """Compose temperature + per-model token-limit kwargs into one dict."""
    kwargs: dict[str, Any] = {"temperature": temperature}
    if model:
        kwargs.update(get_token_limit_kwargs(model, max_tokens))
    kwargs.update(
        build_provider_extra_kwargs(
            binding=binding,
            model=model,
            reasoning_effort=reasoning_effort,
        )
    )
    # Apply model-intrinsic overrides last, matching OpenAICompatProvider. A
    # None value drops the parameter instead of serialising JSON null.
    spec = find_by_name(binding)
    for key, value in model_overrides_for(model, spec).items():
        if value is None:
            kwargs.pop(key, None)
        else:
            kwargs[key] = value
    return kwargs


def build_provider_extra_kwargs(
    *,
    binding: str | None,
    model: str | None,
    reasoning_effort: str | None,
) -> dict[str, Any]:
    """Return provider-specific kwargs for raw OpenAI-compatible agent calls.

    Agentic pipelines stream directly through ``AsyncOpenAI`` so tests can
    inject scripted clients. This helper mirrors the small provider-normalized
    subset that is required before those raw calls: reasoning effort and
    provider-specific thinking flags.
    """
    spec = find_by_name(binding)
    return build_openai_compatible_reasoning_kwargs(
        spec=spec,
        binding=binding,
        model=model,
        reasoning_effort=reasoning_effort,
    )


def can_use_native_tool_calling(
    *, binding: str, model: str | None, api_format: str = "auto"
) -> bool:
    """Whether the current provider supports OpenAI-style function calling.

    Resolution order:

    0. A capability the user declared on the model in Settings wins outright —
       that is the whole point of letting them declare it.
    1. Native provider adapters backed by Anthropic or OpenAI Codex support tools.
    2. Local OpenAI-compatible servers (Ollama, vLLM, LM Studio, llama.cpp,
       Lemonade, OVMS, …) and anything in ``_NATIVE_TOOL_BLOCKED_BINDINGS`` are
       opted out — tool support there depends on the loaded model and is
       unreliable, so the loop falls back to prose.
    3. An explicit ``supports_tools`` capability (provider- or model-level) wins.
    4. Otherwise a registered *cloud* OpenAI-compatible provider is assumed
       tool-capable — function calling is part of that API contract, matching
       the catch-all ``custom`` provider. This keeps newly added cloud
       providers working without a dedicated capability entry, instead of
       silently disabling native tools (the gap that affected e.g. SiliconFlow,
       Gemini, Zhipu, Qianfan, NVIDIA NIM and the Volc/BytePlus coding plans).
       To opt a cloud provider out, add its binding to
       ``_NATIVE_TOOL_BLOCKED_BINDINGS``.
    """
    declared = catalog_capability_override(binding, model, "supports_tools")
    if declared is not None:
        return declared
    spec = find_by_name(binding)
    backend = effective_backend(spec, api_format)
    if spec and backend in _NATIVE_TOOL_BACKENDS:
        return True
    if binding in _NATIVE_TOOL_BLOCKED_BINDINGS or (spec and spec.is_local):
        return False
    if supports_tools(binding, model):
        return True
    return bool(spec and backend == "openai_compat")
