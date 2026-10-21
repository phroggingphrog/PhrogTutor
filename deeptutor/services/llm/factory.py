"""Unified services-layer LLM factory."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator, Mapping
import contextlib
from types import SimpleNamespace
from typing import Any, TypedDict

from deeptutor.config.settings import settings
from deeptutor.services.keypool import primary_api_key
from deeptutor.services.provider_registry import (
    PROVIDERS,
    canonical_provider_name,
    effective_backend,
    find_by_model,
    find_by_name,
    find_gateway,
    model_overrides_for,
)

from .capabilities import get_effective_temperature, supports_vision
from .config import LLMConfig, get_llm_config
from .multimodal import prepare_multimodal_messages
from .types import StreamOutcome
from .utils import is_local_llm_server

DEFAULT_MAX_RETRIES = settings.retry.max_retries
DEFAULT_RETRY_DELAY = settings.retry.base_delay
DEFAULT_EXPONENTIAL_BACKOFF = settings.retry.exponential_backoff


class ApiProviderPreset(TypedDict, total=False):
    """Typed representation of API provider presets."""

    name: str
    base_url: str
    requires_key: bool
    models: list[str]
    binding: str


class LocalProviderPreset(TypedDict, total=False):
    """Typed representation of local provider presets."""

    name: str
    base_url: str
    requires_key: bool
    default_key: str
    binding: str


ProviderPreset = ApiProviderPreset | LocalProviderPreset
ProviderPresetMap = Mapping[str, ProviderPreset]
ProviderPresetBundle = Mapping[str, ProviderPresetMap]


def _resolve_provider_spec(
    *,
    binding: str | None,
    model: str,
    api_key: str | list[str],
    base_url: str | None,
    fallback: str | None,
):
    explicit = find_by_name(binding)
    gateway = find_gateway(
        provider_name=explicit.name if explicit else None,
        api_key=primary_api_key(api_key),
        api_base=base_url or None,
    )
    if explicit and gateway and explicit.name == "openai":
        return gateway
    if explicit:
        return explicit
    if gateway:
        return gateway

    model_spec = find_by_model(model)
    if model_spec:
        return model_spec

    if is_local_llm_server(base_url):
        if base_url and "11434" in base_url:
            return find_by_name("ollama") or find_by_name("vllm")
        return find_by_name("vllm") or find_by_name("ollama")

    return find_by_name(fallback) or find_by_name("openai")


def _url_matches_current(explicit_url: str | None, current: LLMConfig) -> bool:
    if explicit_url is None:
        return True
    return explicit_url in {
        url for url in (current.base_url, current.effective_url) if url is not None
    }


def _binding_matches_current(binding: str | None, current: LLMConfig) -> bool:
    if not binding:
        return True
    canonical = canonical_provider_name(binding) or binding
    return canonical in {current.binding, current.provider_name}


def _matching_current_config(
    *,
    model: str,
    api_key: str | list[str],
    base_url: str | None,
    api_version: str | None,
    binding: str | None,
) -> LLMConfig | None:
    """Return the active config when explicit call fields came from it.

    Several agent call sites pass model/api_key/base_url/binding explicitly
    after reading ``get_llm_config()``. Treat those fields as a partial
    override, not as a request to drop profile-only settings such as
    extra_headers or reasoning_effort.
    """
    with contextlib.suppress(Exception):
        current = get_llm_config()
        if (
            model == current.model
            and api_key == current.api_key
            and _url_matches_current(base_url, current)
            and (api_version is None or api_version == current.api_version)
            and _binding_matches_current(binding, current)
        ):
            return current
    return None


def _resolve_call_config(
    *,
    model: str | None,
    api_key: str | list[str] | None,
    base_url: str | None,
    api_version: str | None,
    binding: str | None,
    extra_headers: dict[str, str] | None,
    reasoning_effort: str | None,
) -> tuple[LLMConfig, Any]:
    if model and api_key is not None and (base_url is not None or binding is not None):
        current = _matching_current_config(
            model=model,
            api_key=api_key,
            base_url=base_url,
            api_version=api_version,
            binding=binding,
        )
        merged_headers = dict(current.extra_headers or {}) if current is not None else {}
        if extra_headers:
            merged_headers.update(extra_headers)
        resolved_reasoning_effort = (
            reasoning_effort
            if reasoning_effort is not None
            else current.reasoning_effort
            if current is not None
            else None
        )
        provider_spec = _resolve_provider_spec(
            binding=binding,
            model=model,
            api_key=api_key,
            base_url=base_url,
            fallback=binding or "openai",
        )
        provider_name = (
            provider_spec.name
            if provider_spec is not None
            else canonical_provider_name(binding) or binding or "openai"
        )
        provider_mode = provider_spec.mode if provider_spec is not None else "standard"
        config = LLMConfig(
            model=model,
            api_key=api_key,
            base_url=base_url,
            effective_url=base_url,
            binding=provider_name,
            provider_name=provider_name,
            provider_mode=provider_mode,
            api_version=api_version,
            extra_headers=merged_headers,
            wire_api=current.wire_api if current is not None else "auto",
            api_format=current.api_format if current is not None else "auto",
            reasoning_effort=resolved_reasoning_effort,
        )
        return config, provider_spec

    current = get_llm_config()
    merged_headers = dict(getattr(current, "extra_headers", None) or {})
    if extra_headers:
        merged_headers.update(extra_headers)

    resolved_model = model or current.model
    resolved_api_key = current.api_key if api_key is None else api_key
    resolved_base_url = base_url if base_url is not None else current.base_url
    resolved_effective_url = base_url if base_url is not None else current.effective_url
    resolved_api_version = api_version if api_version is not None else current.api_version
    binding_hint = binding or current.provider_name or current.binding
    provider_spec = _resolve_provider_spec(
        binding=binding_hint,
        model=resolved_model,
        api_key=resolved_api_key,
        base_url=resolved_effective_url,
        fallback=current.provider_name or current.binding,
    )
    provider_name = provider_spec.name if provider_spec is not None else current.provider_name
    provider_mode = provider_spec.mode if provider_spec is not None else current.provider_mode
    resolved_binding = provider_name or binding_hint or current.binding or "openai"

    config = current.model_copy(
        update={
            "model": resolved_model,
            "api_key": resolved_api_key,
            "base_url": resolved_base_url,
            "effective_url": resolved_effective_url,
            "binding": resolved_binding,
            "provider_name": provider_name or resolved_binding,
            "provider_mode": provider_mode,
            "api_version": resolved_api_version,
            "extra_headers": merged_headers,
            "wire_api": current.wire_api,
            "api_format": current.api_format,
            "reasoning_effort": (
                reasoning_effort if reasoning_effort is not None else current.reasoning_effort
            ),
        }
    )
    return config, provider_spec


def _capability_binding(config: LLMConfig, provider_spec: Any) -> str:
    backend = effective_backend(provider_spec, config.api_format)
    if backend == "anthropic":
        return "anthropic"
    if backend == "azure_openai":
        return "azure_openai"
    return (
        getattr(provider_spec, "name", None) or config.provider_name or config.binding or "openai"
    )


def _build_messages(
    prompt: str,
    system_prompt: str,
    messages: list[dict[str, Any]] | None,
) -> list[dict[str, Any]]:
    if messages is not None:
        return messages
    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": prompt},
    ]


def _apply_inline_image_data(
    messages: list[dict[str, Any]],
    *,
    binding: str,
    model: str,
    image_data: str | None,
    image_mime_type: str = "image/png",
    image_filename: str = "image.png",
) -> list[dict[str, Any]]:
    if not image_data:
        return messages

    attachment = SimpleNamespace(
        type="image",
        base64=image_data,
        filename=image_filename,
        mime_type=image_mime_type,
    )
    result = prepare_multimodal_messages(messages, [attachment], binding=binding, model=model)
    return result.messages


def _pi_kwargs(kwargs: dict[str, Any], model: str | None = None, spec: Any = None) -> dict[str, Any]:
    """The request fields the pi bridge understands; images ride in ``messages``.

    Model-intrinsic overrides apply last, whatever route serves the model (a Kimi model rejects an explicit
    ``temperature``): a ``None`` value drops the parameter.
    """
    request = {k: kwargs[k] for k in ("temperature", "max_tokens", "max_completion_tokens", "reasoning_effort", "tools") if kwargs.get(k) is not None}
    for key, value in model_overrides_for(model, spec).items():
        if value is None:
            request.pop(key, None)
        else:
            request[key] = value
    if "temperature" in request and model:  # o-series and gpt-5 accept only their fixed temperature
        request["temperature"] = get_effective_temperature(getattr(spec, "name", None) or "openai", model, request["temperature"])
    return request


async def _pi_request(
    config: LLMConfig,
    provider_spec: Any,
    prompt: str,
    system_prompt: str,
    messages: list[dict[str, Any]] | None,
    kwargs: dict[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """The messages (images folded in) and pi keyword arguments for one resolved call."""
    request_messages = _apply_inline_image_data(
        _build_messages(prompt, system_prompt, messages),
        binding=_capability_binding(config, provider_spec),
        model=config.model,
        image_data=kwargs.get("image_data"),
        image_mime_type=str(kwargs.get("image_mime_type") or "image/png"),
        image_filename=str(kwargs.get("image_filename") or "image.png"),
    )
    effort = kwargs.get("reasoning_effort") or config.reasoning_effort
    return request_messages, _pi_kwargs({**kwargs, **({"reasoning_effort": effort} if effort else {})}, config.model, provider_spec)


async def complete(
    prompt: str,
    system_prompt: str = "You are a helpful assistant.",
    model: str | None = None,
    api_key: str | list[str] | None = None,
    base_url: str | None = None,
    api_version: str | None = None,
    binding: str | None = None,
    messages: list[dict[str, Any]] | None = None,
    **kwargs: Any,
) -> str:
    """One model call on the pi engine, against the active profile (or the gateway) with ``model`` overridden."""
    from deeptutor.services.llm import pi_bridge

    config, provider_spec = _resolve_call_config(
        model=model,
        api_key=api_key,
        base_url=base_url,
        api_version=api_version,
        binding=binding,
        extra_headers=kwargs.get("extra_headers"),
        reasoning_effort=kwargs.get("reasoning_effort"),
    )
    request_messages, pi_args = await _pi_request(config, provider_spec, prompt, system_prompt, messages, kwargs)
    return await pi_bridge.complete(prompt, system_prompt, config.model, request_messages, **pi_args)


async def complete_with_config(
    config: LLMConfig,
    prompt: str,
    system_prompt: str = "You are a helpful assistant.",
    messages: list[dict[str, Any]] | None = None,
    **kwargs: Any,
) -> str:
    """Complete using only the supplied, fully resolved configuration (a settings test runs the real path)."""
    forbidden = {
        "model",
        "api_key",
        "base_url",
        "api_version",
        "binding",
        "effective_url",
        "extra_headers",
        "provider_mode",
        "provider_name",
        "reasoning_effort",
        "wire_api",
    }.intersection(kwargs)
    if forbidden:
        names = ", ".join(sorted(forbidden))
        raise TypeError(f"complete_with_config does not accept config overrides: {names}")

    from deeptutor.services.llm import pi_bridge
    from deeptutor.services.llm.pi_connection import (
        catalog_connections_enabled,
        codex_model_slug,
        is_codex,
        resolve_connection,
    )

    provider_spec = _resolve_provider_spec(
        binding=config.provider_name or config.binding,
        model=config.model,
        api_key=config.api_key,
        base_url=config.effective_url or config.base_url,
        fallback=config.provider_name or config.binding,
    )
    request_messages, pi_args = await _pi_request(config, provider_spec, prompt, system_prompt, messages, kwargs)
    connection = await resolve_connection(config) if catalog_connections_enabled() else None
    model = codex_model_slug(config.model) if is_codex(config) else config.model
    return await pi_bridge.complete(
        prompt, system_prompt, model, request_messages, **({"connection": connection} if connection else {}), **pi_args
    )


async def stream(
    prompt: str,
    system_prompt: str = "You are a helpful assistant.",
    model: str | None = None,
    api_key: str | list[str] | None = None,
    base_url: str | None = None,
    api_version: str | None = None,
    binding: str | None = None,
    messages: list[dict[str, Any]] | None = None,
    outcome: StreamOutcome | None = None,
    **kwargs: Any,
) -> AsyncGenerator[str, None]:
    """Stream a completion as text chunks.

    ``outcome``, when given, is filled with the terminal reason and usage once the stream ends. A caller that
    parses the streamed text needs it to tell a complete response from one cut off at ``max_tokens`` (#1545).
    """
    from deeptutor.services.llm import pi_bridge

    config, provider_spec = _resolve_call_config(
        model=model,
        api_key=api_key,
        base_url=base_url,
        api_version=api_version,
        binding=binding,
        extra_headers=kwargs.get("extra_headers"),
        reasoning_effort=kwargs.get("reasoning_effort"),
    )
    request_messages, pi_args = await _pi_request(config, provider_spec, prompt, system_prompt, messages, kwargs)
    async for part in pi_bridge.stream(prompt, system_prompt, config.model, request_messages, outcome, **pi_args):
        yield part


async def fetch_models(
    binding: str,
    base_url: str = "",
    api_key: str | None = None,
    api_format: str = "auto",
) -> list[str]:
    if is_local_llm_server(base_url):
        from . import local_provider

        return await local_provider.fetch_models(base_url, api_key)

    from . import cloud_provider

    return await cloud_provider.fetch_models(base_url, api_key, binding, api_format=api_format)


def _build_api_provider_presets() -> dict[str, ApiProviderPreset]:
    presets: dict[str, ApiProviderPreset] = {}
    for spec in PROVIDERS:
        if spec.is_local:
            continue
        presets[spec.name] = {
            "name": spec.label,
            "base_url": spec.default_api_base,
            "requires_key": not spec.is_oauth,
            "models": [],
            "binding": spec.name,
        }
    return presets


def _build_local_provider_presets() -> dict[str, LocalProviderPreset]:
    presets: dict[str, LocalProviderPreset] = {}
    for spec in PROVIDERS:
        if not spec.is_local:
            continue
        presets[spec.name] = {
            "name": spec.label,
            "base_url": spec.default_api_base,
            "requires_key": False,
            "default_key": "sk-no-key-required",
            "binding": spec.name,
        }
    return presets


API_PROVIDER_PRESETS: dict[str, ApiProviderPreset] = _build_api_provider_presets()
LOCAL_PROVIDER_PRESETS: dict[str, LocalProviderPreset] = _build_local_provider_presets()


def get_provider_presets() -> ProviderPresetBundle:
    return {
        "api": API_PROVIDER_PRESETS,
        "local": LOCAL_PROVIDER_PRESETS,
    }


__all__ = [
    "complete",
    "stream",
    "fetch_models",
    "get_provider_presets",
    "API_PROVIDER_PRESETS",
    "LOCAL_PROVIDER_PRESETS",
    "DEFAULT_MAX_RETRIES",
    "DEFAULT_RETRY_DELAY",
    "DEFAULT_EXPONENTIAL_BACKOFF",
]

