"""Read and write the model gateway: ``providers.omni`` in ``pi_engine/agent-dir/models.json``.

The gateway is one OpenAI-compatible (or Anthropic-style) endpoint the pi engine uses when the model source is
``gateway``. The file is the engine's own config, so unrelated keys are preserved. pi runs a value that starts with
``!`` as a shell command and expands ``$VAR``, so keys and headers beginning with either are refused here.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

AGENT_DIR = Path(__file__).resolve().parents[2] / "pi_engine" / "agent-dir"
GATEWAY_FILE = AGENT_DIR / "models.json"
EXAMPLE_FILE = AGENT_DIR / "models.example.json"
PROVIDER = "omni"
APIS = ("openai-completions", "openai-responses", "anthropic-messages")


class GatewayError(ValueError):
    """The submitted gateway cannot be saved."""


def _load(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def read() -> dict[str, Any]:
    """The gateway as the Settings page shows it. The key is never returned, only whether one is set."""
    exists = GATEWAY_FILE.exists()
    provider = (_load(GATEWAY_FILE).get("providers") or {}).get(PROVIDER) or {}
    key = str(provider.get("apiKey") or "")
    return {
        "configured": bool(exists and provider.get("baseUrl") and key and key != "YOUR_API_KEY"),
        "file_exists": exists,
        "base_url": str(provider.get("baseUrl") or ""),
        "api": provider.get("api") if provider.get("api") in APIS else "openai-completions",
        "has_key": bool(key) and key != "YOUR_API_KEY",
        "headers": {str(k): str(v) for k, v in (provider.get("headers") or {}).items()},
        "models": [
            {"id": str(m.get("id")), "name": str(m.get("name") or m.get("id"))}
            for m in provider.get("models") or []
            if isinstance(m, dict) and m.get("id")
        ],
    }


def _safe_value(label: str, value: str) -> str:
    if value.startswith(("!", "$")):
        raise GatewayError(f"{label} may not start with ! or $ (pi would run it).")
    return value


def write(
    *,
    base_url: str,
    api: str,
    api_key: str | None,
    headers: dict[str, str],
    models: list[str],
) -> dict[str, Any]:
    """Save the gateway. ``api_key=None`` keeps the stored key; model ids replace the list, keeping known details."""
    base_url = (base_url or "").strip().rstrip("/")
    if not base_url.startswith(("http://", "https://")):
        raise GatewayError("The base URL must start with http:// or https://.")
    if api not in APIS:
        raise GatewayError(f"The API format must be one of {', '.join(APIS)}.")
    clean_headers = {}
    for name, value in (headers or {}).items():
        name = str(name).strip()
        if not name:
            continue
        clean_headers[name] = _safe_value(f"Header {name}", str(value))
    ids = []
    for item in models or []:
        model_id = str(item).strip()
        if model_id and model_id not in ids:
            ids.append(model_id)
    if not ids:
        raise GatewayError("Add at least one model id.")

    data = _load(GATEWAY_FILE) or _load(EXAMPLE_FILE)
    providers = data.setdefault("providers", {})
    provider = providers.setdefault(PROVIDER, {})
    previous = {m.get("id"): m for m in provider.get("models") or [] if isinstance(m, dict)}
    if api_key is not None and api_key != "":
        provider["apiKey"] = _safe_value("The API key", api_key.strip())
    elif not provider.get("apiKey") or provider.get("apiKey") == "YOUR_API_KEY":
        raise GatewayError("Enter the gateway's API key.")
    provider.update({"baseUrl": base_url, "api": api})
    if clean_headers:
        provider["headers"] = clean_headers
    else:
        provider.pop("headers", None)
    provider["models"] = [previous.get(i) or {"id": i, "name": i} for i in ids]

    GATEWAY_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = GATEWAY_FILE.with_suffix(".json.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)  # holds a key: owner-only
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(json.dumps(data, indent=2, ensure_ascii=False) + "\n")
    os.replace(tmp, GATEWAY_FILE)
    return read()
