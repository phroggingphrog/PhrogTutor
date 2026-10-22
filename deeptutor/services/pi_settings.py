"""Settings for how requests reach the pi engine, kept in ``data/user/settings/pi_engine.json``.

* ``model_source``: ``auto`` (profiles unless a gateway is configured), ``catalog`` (always the profiles chosen in
  Settings) or ``gateway`` (always the gateway in ``pi_engine/agent-dir/models.json``).
* ``engine_idle_minutes``: minutes without a model call after which the engine process exits to free its memory
  (0 keeps it running).

The ``PI_TUTOR_LLM_FROM_CATALOG`` and ``PI_TUTOR_ENGINE_IDLE_S`` environment variables still win, so a deployment can
pin either from its environment.
"""

from __future__ import annotations

import json
import os
from typing import Any

from deeptutor.services.path_service import get_path_service

MODEL_SOURCES = ("auto", "catalog", "gateway")
DEFAULTS: dict[str, Any] = {"model_source": "auto", "engine_idle_minutes": 10}


def _file():
    return get_path_service().get_settings_file("pi_engine")


def load() -> dict[str, Any]:
    settings = dict(DEFAULTS)
    try:
        stored = json.loads(_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return settings
    if isinstance(stored, dict):
        if stored.get("model_source") in MODEL_SOURCES:
            settings["model_source"] = stored["model_source"]
        minutes = stored.get("engine_idle_minutes")
        if isinstance(minutes, int) and not isinstance(minutes, bool) and 0 <= minutes <= 24 * 60:
            settings["engine_idle_minutes"] = minutes
    return settings


def save(*, model_source: str | None = None, engine_idle_minutes: int | None = None) -> dict[str, Any]:
    settings = load()
    if model_source is not None:
        if model_source not in MODEL_SOURCES:
            raise ValueError(f"model_source must be one of {', '.join(MODEL_SOURCES)}")
        settings["model_source"] = model_source
    if engine_idle_minutes is not None:
        if isinstance(engine_idle_minutes, bool) or not 0 <= engine_idle_minutes <= 24 * 60:
            raise ValueError("engine_idle_minutes must be between 0 and 1440")
        settings["engine_idle_minutes"] = int(engine_idle_minutes)
    path = _file()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(settings, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)
    return settings


def model_source() -> str:
    return load()["model_source"]


def engine_idle_seconds() -> float:
    env = os.environ.get("PI_TUTOR_ENGINE_IDLE_S", "").strip()
    if env:
        try:
            return max(0.0, float(env))
        except ValueError:
            pass
    return float(load()["engine_idle_minutes"]) * 60.0
