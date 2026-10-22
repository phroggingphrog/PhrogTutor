"""Settings → Engines: where model calls go, the model gateway, the pi engine and optional engines.

Mounted at ``/api/settings/engines``. Admin-gated: it changes which endpoint receives every prompt, stores a key, and
installs packages into the app's Python environment.
"""

from __future__ import annotations

import os
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from deeptutor.api.routers.auth import require_admin
from deeptutor.services import model_gateway, optional_engines, pi_settings
from deeptutor.services.llm import pi_bridge
from deeptutor.services.llm.pi_connection import catalog_connections_enabled

router = APIRouter(dependencies=[Depends(require_admin)])


class EngineSettingsPayload(BaseModel):
    model_source: str | None = None
    engine_idle_minutes: int | None = Field(default=None, ge=0, le=1440)


class GatewayPayload(BaseModel):
    base_url: str
    api: str = "openai-completions"
    api_key: str | None = None  # None or "": keep the stored key
    headers: dict[str, str] = Field(default_factory=dict)
    models: list[str] = Field(default_factory=list)


class InstallPayload(BaseModel):
    engine: str


def _overview() -> dict[str, Any]:
    stored = pi_settings.load()
    pinned = os.environ.get("PI_TUTOR_LLM_FROM_CATALOG", "").strip()
    return {
        "model_source": stored["model_source"],
        "engine_idle_minutes": stored["engine_idle_minutes"],
        "effective_source": "catalog" if catalog_connections_enabled() else "gateway",
        "pinned_by_environment": bool(pinned),
        "idle_pinned_by_environment": bool(os.environ.get("PI_TUTOR_ENGINE_IDLE_S", "").strip()),
        "gateway": model_gateway.read(),
        "runtime": pi_bridge.engine_status(),
        "engines": optional_engines.status(),
        "install": optional_engines.job(),
    }


@router.get("")
async def get_overview() -> dict[str, Any]:
    return _overview()


@router.put("/settings")
async def update_settings(payload: EngineSettingsPayload) -> dict[str, Any]:
    try:
        pi_settings.save(model_source=payload.model_source, engine_idle_minutes=payload.engine_idle_minutes)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if payload.engine_idle_minutes is not None:
        await pi_bridge.restart_engines()  # the idle timer is read when the engine starts
    return _overview()


@router.put("/gateway")
async def update_gateway(payload: GatewayPayload) -> dict[str, Any]:
    try:
        model_gateway.write(
            base_url=payload.base_url,
            api=payload.api,
            api_key=payload.api_key,
            headers=payload.headers,
            models=payload.models,
        )
    except model_gateway.GatewayError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    await pi_bridge.restart_engines()  # the engine reads the gateway file once, when it starts
    return _overview()


@router.post("/gateway/test")
async def test_gateway() -> dict[str, Any]:
    gateway = model_gateway.read()
    if not gateway["configured"]:
        raise HTTPException(status_code=400, detail="Save the gateway first.")
    try:
        text = await pi_bridge.complete(
            "Reply with the single word: ok", "You are a connectivity check.", gateway["models"][0]["id"], None,
            max_tokens=16, force_gateway=True,
        )
    except Exception as exc:  # noqa: BLE001 - surface any transport error to the admin who is testing
        return {"ok": False, "error": str(exc)[-400:]}
    return {"ok": bool(text.strip()), "model": gateway["models"][0]["id"], "reply": text.strip()[:80]}


@router.post("/restart")
async def restart_engine() -> dict[str, Any]:
    await pi_bridge.restart_engines()
    return _overview()


@router.post("/install")
async def install_engine(payload: InstallPayload) -> dict[str, Any]:
    try:
        await optional_engines.install(payload.engine)
    except optional_engines.EngineError as exc:
        raise HTTPException(status_code=409 if "running" in str(exc) else 400, detail=str(exc)) from exc
    return _overview()
