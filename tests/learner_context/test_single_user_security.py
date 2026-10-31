from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from deeptutor.api.routers import learner_context as routes
from deeptutor.learner_context import credentials
from deeptutor.multi_user.context import reset_current_user, set_current_user
from deeptutor.multi_user.models import CurrentUser, LOCAL_ADMIN_ID, UserScope


def _app():
    app = FastAPI()
    app.include_router(routes.integration_router, prefix="/api/learner-context/v1/integrations")
    return app


def _local_owner(tmp_path):
    return CurrentUser(
        id=LOCAL_ADMIN_ID,
        username="local",
        role="admin",
        scope=UserScope(kind="admin", user_id=LOCAL_ADMIN_ID, root=tmp_path / "data"),
    )


def _request(client, path="/api/learner-context/v1/integrations/profile", headers=None):
    return client.get(path, headers=headers or {})


def test_auth_disabled_single_user_uses_stable_local_owner_and_token(tmp_path, monkeypatch):
    from deeptutor.multi_user import paths

    monkeypatch.setattr(paths, "SYSTEM_ROOT", tmp_path / "data" / "system")
    monkeypatch.setattr(paths, "ADMIN_WORKSPACE_ROOT", tmp_path / "data")
    monkeypatch.setattr(paths, "USERS_ROOT", tmp_path / "data" / "users")
    monkeypatch.setattr(routes, "AUTH_ENABLED", False)
    monkeypatch.setenv("PI_TUTOR_LEARNER_CONTEXT_CREDENTIALS_DB", str(tmp_path / "override.db"))
    original = set_current_user(_local_owner(tmp_path))
    try:
        created = credentials.create(LOCAL_ADMIN_ID, "local harness", 10, ("read",))
        token = credentials.load_token(LOCAL_ADMIN_ID, created["id"])
        assert credentials.database_path(LOCAL_ADMIN_ID).name == f"{LOCAL_ADMIN_ID}.sqlite3"
    finally:
        reset_current_user(original)

    monkeypatch.setattr(routes, "_trusted_local_request", lambda _request: True)
    client = TestClient(_app(), client=("127.0.0.1", 50000))
    response = client.get(
        "/api/learner-context/v1/integrations/profile",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200
    assert credentials.current_owner_id() == LOCAL_ADMIN_ID
    assert token and token.startswith(f"dtlc_{LOCAL_ADMIN_ID}.")
    assert token not in response.text


def test_single_user_route_rejects_proxy_identity_headers_and_non_loopback(tmp_path, monkeypatch):
    from deeptutor.multi_user import paths

    monkeypatch.setattr(paths, "SYSTEM_ROOT", tmp_path / "data" / "system")
    monkeypatch.setattr(paths, "ADMIN_WORKSPACE_ROOT", tmp_path / "data")
    monkeypatch.setattr(paths, "USERS_ROOT", tmp_path / "data" / "users")
    monkeypatch.setattr(routes, "AUTH_ENABLED", False)
    from starlette.requests import Request

    forged = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/",
            "headers": [(b"cf-access-jwt-assertion", b"forged"), (b"x-forwarded-for", b"127.0.0.1")],
            "client": ("127.0.0.1", 8000),
            "server": ("localhost", 18051),
            "scheme": "http",
            "query_string": b"",
        }
    )
    assert not routes._trusted_local_request(forged)
    client = TestClient(_app(), client=("127.0.0.1", 50000))
    response = client.get(
        "/api/learner-context/v1/integrations/profile",
        headers={"cf-access-jwt-assertion": "forged", "X-Forwarded-For": "127.0.0.1"},
    )
    assert response.status_code == 403

    public = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/",
            "headers": [],
            "client": ("203.0.113.5", 8000),
            "server": ("localhost", 18051),
            "scheme": "http",
            "query_string": b"",
        }
    )
    assert not routes._trusted_local_request(public)

    named = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/",
            "headers": [],
            "client": ("testclient", 8000),
            "server": ("localhost", 18051),
            "scheme": "http",
            "query_string": b"",
        }
    )
    assert not routes._trusted_local_request(named)


def test_unknown_owner_token_does_not_create_database_or_secret_path(tmp_path, monkeypatch):
    from deeptutor.multi_user import paths

    monkeypatch.setattr(paths, "SYSTEM_ROOT", tmp_path / "data" / "system")
    monkeypatch.setattr(paths, "ADMIN_WORKSPACE_ROOT", tmp_path / "data")
    monkeypatch.setattr(paths, "USERS_ROOT", tmp_path / "data" / "users")
    owner = "u_unknown"
    assert credentials.load_token(owner, "a" * 24) is None
    assert not credentials.database_path(owner).exists()
    assert not (paths.SYSTEM_ROOT / "user-secrets" / owner).exists()

def test_single_user_has_no_anonymous_routes_and_read_only_secret_cannot_write(tmp_path, monkeypatch):
    from deeptutor.multi_user import paths

    monkeypatch.setattr(paths, "SYSTEM_ROOT", tmp_path / "data" / "system")
    monkeypatch.setattr(paths, "ADMIN_WORKSPACE_ROOT", tmp_path / "data")
    monkeypatch.setattr(paths, "USERS_ROOT", tmp_path / "data" / "users")
    monkeypatch.setattr(routes, "AUTH_ENABLED", False)
    client = TestClient(_app(), client=("127.0.0.1", 50000))
    for url in (
        "/api/learner-context/v1/integrations/profile",
        "/api/learner-context/v1/integrations/prompt-context",
    ):
        assert _request(client, url).status_code == 401

    original = set_current_user(_local_owner(tmp_path))
    try:
        created = credentials.create(LOCAL_ADMIN_ID, "local read", 10, ("read",))
        token = credentials.load_token(LOCAL_ADMIN_ID, created["id"])
    finally:
        reset_current_user(original)
    assert token
    write = client.post(
        "/api/learner-context/v1/integrations/interactions",
        headers={"Authorization": f"Bearer {token}"},
        json={"event_id": "synthetic", "source": "pi", "user_message": "synthetic only"},
    )
    assert write.status_code == 403
