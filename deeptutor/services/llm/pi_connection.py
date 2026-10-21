"""Describe an LLM profile to the pi engine, so one catalog entry drives chat, settings tests and tools.

The engine used to read its own gateway from ``agent-dir/models.json`` / ``PI_TUTOR_LLM_*`` while the
catalog held a second copy of the same endpoint. A request can now carry a ``connection``: the pi-ai
API format to speak, the base URL, the key and any headers. ``pi_engine/convert.ts`` uses it in place of
the gateway.

``PI_TUTOR_LLM_FROM_CATALOG`` decides: ``1`` forces it on, ``0`` forces it off. Unset, it is on only when
no gateway is configured (no ``PI_TUTOR_LLM_BASE_URL`` and no ``agent-dir/models.json``), so a fresh
install uses the catalog while an existing gateway setup keeps working untouched.
Profiles pi cannot serve yet (OAuth sign-ins, Azure deployments) return ``None`` and use the old path.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from deeptutor.services.provider_registry import effective_backend, find_by_name

from .config import LLMConfig


_GATEWAY_FILE = Path(__file__).resolve().parents[3] / "pi_engine" / "agent-dir" / "models.json"


def catalog_connections_enabled() -> bool:
    setting = os.environ.get("PI_TUTOR_LLM_FROM_CATALOG", "").strip().lower()
    if setting:
        return setting in {"1", "true", "yes"}
    return not os.environ.get("PI_TUTOR_LLM_BASE_URL", "").strip() and not _GATEWAY_FILE.exists()


def _pi_api(config: LLMConfig, backend: str) -> str | None:
    if backend == "anthropic":
        return "anthropic-messages"
    if backend == "openai_compat":
        return "openai-responses" if config.wire_api == "responses" else "openai-completions"
    return None  # azure_openai and the OAuth providers: not served through pi yet


def pi_connection(config: LLMConfig) -> dict[str, Any] | None:
    """The ``connection`` object for one resolved profile, or None when pi cannot serve it."""
    spec = find_by_name(config.provider_name) or find_by_name(config.binding)
    if spec is not None and spec.is_oauth:
        return None
    api = _pi_api(config, effective_backend(spec, config.api_format))
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
