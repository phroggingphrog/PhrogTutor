"""Describe an LLM profile to the pi engine, so one catalog entry drives chat, settings tests and tools.

The engine used to read its own gateway from ``agent-dir/models.json`` / ``PI_TUTOR_LLM_*`` while the
catalog held a second copy of the same endpoint. A request can now carry a ``connection``: the pi-ai
API format to speak, the base URL, the key and any headers. ``pi_engine/convert.ts`` uses it in place of
the gateway.

``PI_TUTOR_LLM_FROM_CATALOG`` decides: ``1`` forces it on, ``0`` forces it off. Unset, it is on only when
no gateway is configured (no ``PI_TUTOR_LLM_BASE_URL`` and no ``agent-dir/models.json``), so a fresh
install uses the catalog while an existing gateway setup keeps working untouched.
Profiles pi cannot serve yet (Azure with ``api-version=preview``) return ``None``
and use the old path. The ChatGPT/Codex sign-in stays managed by ``services/codex_auth`` (login, refresh,
per-user storage); only the inference call goes through pi, with the access token that service hands out.
"""

from __future__ import annotations

import os
from pathlib import Path
import re
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from deeptutor.services.provider_registry import effective_backend, find_by_name

from .config import LLMConfig


_GATEWAY_FILE = Path(__file__).resolve().parents[3] / "pi_engine" / "agent-dir" / "models.json"


def catalog_connections_enabled() -> bool:
    setting = os.environ.get("PI_TUTOR_LLM_FROM_CATALOG", "").strip().lower()
    if setting:
        return setting in {"1", "true", "yes"}
    return not os.environ.get("PI_TUTOR_LLM_BASE_URL", "").strip() and not _GATEWAY_FILE.exists()


_ENDPOINT_SUFFIXES = ("/chat/completions", "/completions", "/responses", "/embeddings")
_DEPLOYMENT_SEGMENT = re.compile(r"/deployments/[^/]+")


def normalize_azure_base_url(api_base: str) -> str:
    """Reduce any Azure OpenAI endpoint spelling to the ``/openai/v1`` surface.

    The portal hands out classic endpoints such as
    ``https://res.openai.azure.com/openai/deployments/<name>/chat/completions?api-version=...``;
    appending ``/openai/v1`` to those verbatim gives an unreachable path.
    """
    raw = (api_base or "").strip()
    if not raw:
        return raw
    if not re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", raw):
        raw = f"https://{raw}"
    parsed = urlsplit(raw)
    path = parsed.path.rstrip("/")
    stripped = True
    while stripped:
        stripped = False
        for suffix in _ENDPOINT_SUFFIXES:
            if path.endswith(suffix):
                path = path[: -len(suffix)].rstrip("/")
                stripped = True
    path = _DEPLOYMENT_SEGMENT.sub("", path).rstrip("/")
    for surface in ("/openai/v1", "/openai"):
        if path.endswith(surface):
            path = path[: -len(surface)].rstrip("/")
            break
    return urlunsplit((parsed.scheme, parsed.netloc, f"{path}/openai/v1", "", ""))


def _azure_connection(config: LLMConfig) -> dict[str, Any] | None:
    """Azure serves the Responses API at ``/openai/v1``; the model name is the deployment."""
    key = config.get_api_key()
    base_url = normalize_azure_base_url(config.effective_url or config.base_url or "")
    if not key or not base_url or (config.api_version or "").strip().lower() == "preview":
        return None  # the ``preview`` query parameter has no place in a pi base URL
    headers = {"api-key": key, **(config.extra_headers or {})}
    return {"api": "openai-responses", "provider": "azure_openai", "baseUrl": base_url, "apiKey": key, "headers": headers}


def _pi_api(config: LLMConfig, backend: str) -> str | None:
    if backend == "anthropic":
        return "anthropic-messages"
    if backend == "openai_compat":
        return "openai-responses" if config.wire_api == "responses" else "openai-completions"
    return None  # the OAuth providers: not served through pi yet


CODEX_BASE_URL = "https://chatgpt.com/backend-api"


def codex_model_slug(model: str) -> str:
    return model.split("/", 1)[1] if model.startswith(("openai-codex/", "openai_codex/")) else model


async def codex_connection(config: LLMConfig) -> dict[str, Any]:
    """The signed-in ChatGPT account as a pi connection. Raises ``CodexAuthError`` when not signed in."""
    from deeptutor.services.codex_auth import get_codex_oauth_service

    service = get_codex_oauth_service()
    token = await service.get_token()
    service.validate_runtime_profile(token, codex_model_slug(config.model), config.reasoning_effort)
    return {
        "api": "openai-codex-responses",
        "provider": "openai-codex",
        "baseUrl": os.environ.get("PI_TUTOR_CODEX_BASE_URL", CODEX_BASE_URL),
        "apiKey": token.access_token,
        "headers": None,
        "transport": "sse",
    }


def is_codex(config: LLMConfig) -> bool:
    spec = find_by_name(config.provider_name) or find_by_name(config.binding)
    return spec is not None and spec.backend == "openai_codex"


async def resolve_connection(config: LLMConfig) -> dict[str, Any] | None:
    """:func:`pi_connection`, plus the sign-in profiles whose token has to be fetched first."""
    if is_codex(config):
        return await codex_connection(config)
    return pi_connection(config)


def pi_connection(config: LLMConfig) -> dict[str, Any] | None:
    """The ``connection`` object for one resolved profile, or None when pi cannot serve it."""
    spec = find_by_name(config.provider_name) or find_by_name(config.binding)
    if spec is not None and spec.is_oauth:
        return None
    backend = effective_backend(spec, config.api_format)
    if backend == "azure_openai":
        return _azure_connection(config)
    api = _pi_api(config, backend)
    base_url = (config.effective_url or config.base_url or (spec.default_api_base if spec else "") or "").rstrip("/")
    key = config.get_api_key()
    if api is None or not base_url:
        return None
    if api == "anthropic-messages" and base_url.endswith("/v1"):
        base_url = base_url[: -len("/v1")]  # the Anthropic SDK adds /v1/messages itself
    return {
        "api": api,
        "provider": spec.name if spec else "custom",
        "baseUrl": base_url,
        "apiKey": key,
        "headers": dict(config.extra_headers or {}) or None,
    }
