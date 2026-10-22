"""Settings → Engines: model source, gateway file, engine status and optional-engine installs."""

from __future__ import annotations

import asyncio
import json
import stat
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from deeptutor.api.routers import auth as auth_router
from deeptutor.api.routers import engines as engines_router
from deeptutor.services import model_gateway, optional_engines, pi_settings
from deeptutor.services.llm import pi_connection


@pytest.fixture()
def api(monkeypatch, tmp_path):
    settings_dir = tmp_path / "settings"
    settings_dir.mkdir()
    monkeypatch.setattr(pi_settings, "_file", lambda: settings_dir / "pi_engine.json")
    monkeypatch.setattr(optional_engines, "_settings_file", lambda: settings_dir / "extras.json")
    gateway_file = tmp_path / "agent-dir" / "models.json"
    monkeypatch.setattr(model_gateway, "GATEWAY_FILE", gateway_file)
    monkeypatch.setattr(model_gateway, "EXAMPLE_FILE", tmp_path / "missing-example.json")
    monkeypatch.setattr(pi_connection, "_GATEWAY_FILE", gateway_file)
    monkeypatch.delenv("PI_TUTOR_LLM_FROM_CATALOG", raising=False)
    monkeypatch.delenv("PI_TUTOR_LLM_BASE_URL", raising=False)
    monkeypatch.delenv("PI_TUTOR_ENGINE_IDLE_S", raising=False)
    restarts = []

    async def restart():
        restarts.append(True)

    monkeypatch.setattr(engines_router.pi_bridge, "restart_engines", restart)
    app = FastAPI()
    app.include_router(engines_router.router, prefix="/api/settings/engines")
    app.dependency_overrides[auth_router.require_admin] = lambda: None
    client = TestClient(app)
    client.gateway_file, client.restarts = gateway_file, restarts
    return client


def test_model_source_defaults_to_automatic_and_follows_a_gateway_file(api) -> None:
    body = api.get("/api/settings/engines").json()
    assert body["model_source"] == "auto" and body["effective_source"] == "catalog"
    api.gateway_file.parent.mkdir()
    api.gateway_file.write_text("{}")
    assert api.get("/api/settings/engines").json()["effective_source"] == "gateway"


def test_choosing_a_model_source_overrides_the_gateway_file_and_persists(api) -> None:
    api.gateway_file.parent.mkdir()
    api.gateway_file.write_text("{}")
    body = api.put("/api/settings/engines/settings", json={"model_source": "catalog"}).json()
    assert body["model_source"] == "catalog" and body["effective_source"] == "catalog"
    assert pi_connection.catalog_connections_enabled() is True
    body = api.put("/api/settings/engines/settings", json={"model_source": "gateway"}).json()
    assert body["effective_source"] == "gateway" and pi_connection.catalog_connections_enabled() is False


def test_the_environment_variable_still_pins_the_source(api, monkeypatch) -> None:
    api.put("/api/settings/engines/settings", json={"model_source": "gateway"})
    monkeypatch.setenv("PI_TUTOR_LLM_FROM_CATALOG", "1")
    body = api.get("/api/settings/engines").json()
    assert body["pinned_by_environment"] is True and body["effective_source"] == "catalog"


def test_invalid_settings_are_rejected(api) -> None:
    assert api.put("/api/settings/engines/settings", json={"model_source": "elsewhere"}).status_code == 400
    assert api.put("/api/settings/engines/settings", json={"engine_idle_minutes": -1}).status_code == 422


def test_idle_minutes_change_restarts_the_engine_and_feed_the_timer(api) -> None:
    api.put("/api/settings/engines/settings", json={"engine_idle_minutes": 3})
    assert api.restarts and pi_settings.engine_idle_seconds() == 180.0


def test_the_gateway_is_saved_with_an_owner_only_file_and_never_echoes_the_key(api) -> None:
    payload = {
        "base_url": "https://gw.example/v1/",
        "api": "openai-completions",
        "api_key": "sk-secret-value",
        "headers": {"x-route": "go"},
        "models": ["m-one", "m-two", "m-one"],
    }
    body = api.put("/api/settings/engines/gateway", json=payload).json()
    gateway = body["gateway"]
    assert gateway["configured"] and gateway["has_key"] and gateway["base_url"] == "https://gw.example/v1"
    assert [m["id"] for m in gateway["models"]] == ["m-one", "m-two"]
    assert "sk-secret-value" not in json.dumps(body)
    assert stat.S_IMODE(api.gateway_file.stat().st_mode) == 0o600
    assert api.restarts  # the engine reads the file once, so it is restarted
    # saving again without a key keeps the stored one
    api.put("/api/settings/engines/gateway", json={**payload, "api_key": None, "models": ["m-three"]})
    saved = json.loads(api.gateway_file.read_text())["providers"]["omni"]
    assert saved["apiKey"] == "sk-secret-value" and [m["id"] for m in saved["models"]] == ["m-three"]


@pytest.mark.parametrize(
    "change",
    [
        {"base_url": "ftp://gw"},
        {"api": "carrier-pigeon"},
        {"models": []},
        {"api_key": "!rm -rf ~"},
        {"api_key": "$SECRET"},
        {"headers": {"x": "!cmd"}},
    ],
)
def test_unsafe_or_incomplete_gateways_are_refused(api, change) -> None:
    payload = {"base_url": "https://gw.example/v1", "api": "openai-completions", "api_key": "k", "models": ["m"], **change}
    assert api.put("/api/settings/engines/gateway", json=payload).status_code == 400
    assert not api.gateway_file.exists()


def test_the_first_gateway_save_needs_a_key(api) -> None:
    payload = {"base_url": "https://gw.example/v1", "models": ["m"]}
    assert api.put("/api/settings/engines/gateway", json=payload).status_code == 400


def test_testing_an_unsaved_gateway_is_refused(api) -> None:
    assert api.post("/api/settings/engines/gateway/test").status_code == 400


def test_the_gateway_test_reports_a_reply_or_the_error(api, monkeypatch) -> None:
    api.put(
        "/api/settings/engines/gateway",
        json={"base_url": "https://gw.example/v1", "api_key": "k", "models": ["m"]},
    )
    seen = {}

    async def ok(prompt, system, model, messages, **kwargs):
        seen.update(model=model, **kwargs)
        return "ok"

    monkeypatch.setattr(engines_router.pi_bridge, "complete", ok)
    body = api.post("/api/settings/engines/gateway/test").json()
    assert body == {"ok": True, "model": "m", "reply": "ok"} and seen["force_gateway"] is True

    async def boom(*_a, **_k):
        raise RuntimeError("401 unauthorized")

    monkeypatch.setattr(engines_router.pi_bridge, "complete", boom)
    body = api.post("/api/settings/engines/gateway/test").json()
    assert body["ok"] is False and "401" in body["error"]


def test_runtime_status_reports_bun_and_whether_it_runs(api) -> None:
    runtime = api.get("/api/settings/engines").json()["runtime"]
    assert set(runtime) >= {"installed", "version", "running", "memory_mb"} and runtime["running"] is False


def test_only_declared_engines_can_be_installed(api) -> None:
    assert api.post("/api/settings/engines/install", json={"engine": "numpy; rm -rf /"}).status_code == 400


def test_installing_runs_pip_for_the_missing_requirements_and_records_the_choice(api, monkeypatch, tmp_path) -> None:
    commands = []

    class FakeProc:
        returncode = 0

        def __init__(self):
            self.stdout = self._lines()

        async def _lines(self):
            yield b"Collecting thing\n"
            yield b"Successfully installed thing\n"

        async def wait(self):
            return 0

    async def fake_exec(*args, **kwargs):
        commands.append(args)
        return FakeProc()

    monkeypatch.setattr(optional_engines.asyncio, "create_subprocess_exec", fake_exec)
    monkeypatch.setattr(optional_engines, "declared_extras", lambda: {"rag-lightrag": ["not-a-real-pkg-xyz>=1"]})

    async def scenario():
        await optional_engines.install("rag-lightrag")
        for _ in range(50):
            if optional_engines.job()["state"] != "installing":
                break
            await asyncio.sleep(0.05)

    asyncio.run(scenario())
    job = optional_engines.job()
    assert job["state"] == "done" and job["engine"] == "rag-lightrag"
    assert commands and commands[0][1:4] == ("-m", "pip", "install") and commands[0][-1] == "not-a-real-pkg-xyz>=1"
    assert "Successfully installed thing" in job["log"]
    assert optional_engines.recorded_extras() == ["rag-lightrag"]


def test_a_failed_install_is_reported_not_recorded(api, monkeypatch) -> None:
    class FakeProc:
        returncode = 1

        def __init__(self):
            self.stdout = self._lines()

        async def _lines(self):
            yield b"ERROR: no matching distribution\n"

        async def wait(self):
            return 1

    async def fake_exec(*args, **kwargs):
        return FakeProc()

    monkeypatch.setattr(optional_engines.asyncio, "create_subprocess_exec", fake_exec)
    monkeypatch.setattr(optional_engines, "declared_extras", lambda: {"graphrag": ["not-a-real-pkg-xyz>=1"]})

    async def scenario():
        await optional_engines.install("graphrag")
        for _ in range(50):
            if optional_engines.job()["state"] != "installing":
                break
            await asyncio.sleep(0.05)

    asyncio.run(scenario())
    assert optional_engines.job()["state"] == "failed"
    assert optional_engines.recorded_extras() == []


def test_an_installed_engine_is_reported_installed(api, monkeypatch) -> None:
    monkeypatch.setattr(optional_engines, "declared_extras", lambda: {"rag-pageindex": ["pytest>=1"]})
    rows = {r["id"]: r for r in api.get("/api/settings/engines").json()["engines"]}
    assert rows["rag-pageindex"]["installed"] is True
