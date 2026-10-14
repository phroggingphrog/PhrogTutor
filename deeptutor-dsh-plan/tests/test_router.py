import os
from types import SimpleNamespace

import pytest

fastapi = pytest.importorskip("fastapi")
from fastapi import FastAPI
from fastapi.testclient import TestClient


def test_owner_click_and_blocked(tmp_path, monkeypatch):
    monkeypatch.setenv("DSH_PLAN_DB", str(tmp_path / "t.sqlite"))
    monkeypatch.delenv("DSH_BRIDGE_URL", raising=False)
    import importlib
    from dsh_plan import plan_records, router
    importlib.reload(plan_records); importlib.reload(router)
    store = plan_records.Store(str(tmp_path / "t.sqlite"))
    monkeypatch.setattr(router, "Store", lambda: plan_records.Store(str(tmp_path / "t.sqlite")))
    app = FastAPI(); app.include_router(router.router)
    p = store.draft("deeptutor", "plan", "ws1")
    body = {"version": 1, "sha256": p["sha256"]}
    c = TestClient(app)
    app.dependency_overrides[router.require_auth] = lambda: SimpleNamespace(username="owner", role="student")
    assert c.post(f"/{p['plan_id']}/approve", json=body).status_code == 403
    app.dependency_overrides[router.require_auth] = lambda: SimpleNamespace(username="owner", role="admin")
    r = c.post(f"/{p['plan_id']}/approve", json=body).json()
    assert r["state"] == "blocked" and "not configured" in r["error"]  # approval kept, no fallback


def test_get_requires_owner(tmp_path, monkeypatch):
    monkeypatch.setenv("DSH_PLAN_DB", str(tmp_path / "t.sqlite"))
    import importlib
    from dsh_plan import plan_records, router
    importlib.reload(plan_records); importlib.reload(router)
    monkeypatch.setattr(router, "Store", lambda: plan_records.Store(str(tmp_path / "t.sqlite")))
    app = FastAPI(); app.include_router(router.router)
    c = TestClient(app)
    app.dependency_overrides[router.require_auth] = lambda: None
    assert c.get("/plan-x").status_code == 403
