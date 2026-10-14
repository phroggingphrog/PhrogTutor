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
    monkeypatch.setattr(router, "AUTH_ENABLED", True)
    assert c.get("/plan-x").status_code == 403  # login on, no identity
    monkeypatch.setattr(router, "AUTH_ENABLED", False)  # login off: instance owner (Cloudflare Access is the gate)
    assert c.get("/plan-x").status_code == 200


def test_modes_and_callback(tmp_path, monkeypatch):
    monkeypatch.setenv("DSH_PLAN_DB", str(tmp_path / "t.sqlite"))
    tok = "t" * 40
    monkeypatch.setenv("DSH_BRIDGE_TOKEN", tok)
    import importlib
    from dsh_plan import plan_records, router
    importlib.reload(plan_records); importlib.reload(router)
    mk = lambda: plan_records.Store(str(tmp_path / "t.sqlite"))
    monkeypatch.setattr(router, "Store", mk)
    app = FastAPI(); app.include_router(router.router)
    app.dependency_overrides[router.require_auth] = lambda: SimpleNamespace(username="owner", role="admin")
    c = TestClient(app)
    p = mk().draft("deeptutor", "plan", "ws1", source_session_id="chat-1")
    b = {"version": 1, "sha256": p["sha256"]}
    assert c.post(f"/{p['plan_id']}/approve", json={**b, "mode": "bypass"}).status_code == 409  # needs confirm
    assert c.post(f"/{p['plan_id']}/approve", json={**b, "mode": "nope"}).status_code == 422
    r = c.post(f"/{p['plan_id']}/approve", json={**b, "mode": "bypass", "confirm_bypass": True}).json()
    assert mk().approval(r["approval_id"])["permission"] == "danger-full-access"
    cb = {"approval_id": r["approval_id"], "state": "completed", "seq": 5, "result": "done"}
    assert c.post("/callback", json=cb).status_code == 401
    ok = c.post("/callback", json=cb, headers={"authorization": f"Bearer {tok}"}).json()
    assert ok["ok"] and mk().handoff(r["approval_id"])["state"] == "completed"
    again = c.post("/callback", json=cb, headers={"authorization": f"Bearer {tok}"}).json()
    assert again["woke"] is False  # second callback is a no-op
