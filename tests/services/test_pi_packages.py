"""pi package installs are validated, admin-only and visible to the engine's loader."""

from __future__ import annotations

import json

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from deeptutor.api.routers import auth, pi_packages as router_module
from deeptutor.services import pi_packages


@pytest.mark.parametrize(
    "source",
    ["npm:@scope/pi-tools", "npm:pi-tools@1.2.0", "git:github.com/owner/repo@v1", "https://github.com/owner/repo"],
)
def test_accepted_sources(source) -> None:
    assert pi_packages.validate_source(source) == source


@pytest.mark.parametrize(
    "source", ["", "--help", "npm:pkg; rm -rf /", "relative/path", "http://x.test/repo", "git:../etc/passwd", "npm:"]
)
def test_rejected_sources(source) -> None:
    with pytest.raises(pi_packages.PiPackageError):
        pi_packages.validate_source(source)


def test_local_folder_source(tmp_path) -> None:
    assert pi_packages.validate_source(str(tmp_path)) == str(tmp_path)


def test_list_reads_the_engine_settings(tmp_path, monkeypatch) -> None:
    (tmp_path / "settings.json").write_text(
        json.dumps({"defaultProvider": "omni", "packages": ["npm:a", {"source": "npm:b", "skills": []}]})
    )
    monkeypatch.setattr(pi_packages, "AGENT_DIR", tmp_path)
    assert pi_packages.list_packages() == [
        {"source": "npm:a", "filtered": False},
        {"source": "npm:b", "filtered": True},
    ]


def test_routes_surface_errors_and_require_admin(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(pi_packages, "AGENT_DIR", tmp_path)
    calls: list[tuple] = []

    async def fake_run(*args):
        calls.append(args)
        return "done"

    monkeypatch.setattr(pi_packages, "_run", fake_run)
    app = FastAPI()
    app.include_router(router_module.router, prefix="/p")
    app.dependency_overrides[auth.require_admin] = lambda: object()
    client = TestClient(app)
    assert client.post("/p", json={"source": "--help"}).status_code == 400
    assert client.post("/p", json={"source": "npm:pi-tools"}).status_code == 200
    assert calls == [("install", "npm:pi-tools")]
    assert client.post("/p/remove", json={"source": "npm:not-installed"}).status_code == 400


@pytest.mark.asyncio
async def test_real_local_install_round_trip(tmp_path, monkeypatch) -> None:
    if not pi_packages._PI_BIN.exists():
        pytest.skip("pi engine dependencies are not installed")
    package = tmp_path / "pkg"
    (package / "extensions").mkdir(parents=True)
    (package / "package.json").write_text('{"name":"t","version":"1.0.0"}')
    (package / "extensions" / "t.ts").write_text("export default function () {}")
    agent = tmp_path / "agent"
    agent.mkdir()
    (agent / "settings.json").write_text("{}")
    monkeypatch.setattr(pi_packages, "AGENT_DIR", agent)
    await pi_packages.install_package(str(package))
    assert len(pi_packages.list_packages()) == 1
    await pi_packages.remove_package(pi_packages.list_packages()[0]["source"])
    assert pi_packages.list_packages() == []
