from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from deeptutor.api.routers import learner_context as routes
from deeptutor.learner_context import credentials, service
from deeptutor.multi_user.models import UserScope
from deeptutor.services.path_service import PathService


def _integration_test_app():
    app = FastAPI()
    app.include_router(routes.integration_router, prefix="/api/learner-context/v1/integrations")
    return app


def _isolate_users(monkeypatch, tmp_path):
    monkeypatch.setenv("PI_TUTOR_LEARNER_CONTEXT_CREDENTIALS_DB", str(tmp_path / "creds.db"))
    monkeypatch.setattr(
        routes,
        "scope_for_user",
        lambda user_id, is_admin=False: UserScope(
            kind="user", user_id=user_id, root=tmp_path / user_id
        ),
    )
    monkeypatch.setattr(
        routes,
        "ensure_scope_workspace",
        lambda scope: scope.root.mkdir(parents=True, exist_ok=True),
    )
    monkeypatch.setattr(
        routes,
        "get_path_service_for_scope",
        lambda scope: PathService(workspace_root=scope.root),
    )
    monkeypatch.setattr(routes, "get_user_by_id", lambda user_id: (user_id, {"role": "user"}))


def test_disabled_auth_blocks_external_api_and_enabled_auth_requires_bearer(tmp_path, monkeypatch):
    _isolate_users(monkeypatch, tmp_path)
    client = TestClient(_integration_test_app())
    assert client.get("/api/learner-context/v1/integrations/profile").status_code == 503

    monkeypatch.setattr(routes, "AUTH_ENABLED", True)
    assert client.get("/api/learner-context/v1/integrations/profile").status_code == 401

    token = credentials.create("u_test", "read-only", 3, ("read",))
    response = client.get(
        "/api/learner-context/v1/integrations/profile",
        headers={"Authorization": f"Bearer {token['access_token']}"},
    )
    assert response.status_code == 200
    assert response.json()["signals"] == 0


def test_http_profile_is_account_isolated_and_revocation_takes_effect(tmp_path, monkeypatch):
    _isolate_users(monkeypatch, tmp_path)
    monkeypatch.setattr(routes, "AUTH_ENABLED", True)
    for user_id, style in (("u_one", "tried_first"), ("u_two", "accepted")):
        with routes._user_workspace(user_id):
            assert service.record_interaction_style(
                style=style, source="pi", event_id=f"{user_id}-event", quote="private quote"
            )

    one = credentials.create("u_one", "Pi", 3, ("read",))
    two = credentials.create("u_two", "DSH", 3, ("read",))
    client = TestClient(_integration_test_app())
    first_response = client.get(
        "/api/learner-context/v1/integrations/profile",
        headers={"Authorization": f"Bearer {one['access_token']}"},
    )
    second_response = client.get(
        "/api/learner-context/v1/integrations/profile",
        headers={"Authorization": f"Bearer {two['access_token']}"},
    )
    assert first_response.status_code == second_response.status_code == 200
    first, second = first_response.json(), second_response.json()
    assert (first["signals"], first["shares"]) == (1, {"tried_first": 1.0})
    assert (second["signals"], second["shares"]) == (1, {"accepted": 1.0})
    assert first["records"] == second["records"] == []
    assert "private quote" not in str(first) + str(second)

    assert credentials.revoke("u_one", one["id"])
    revoked = client.get(
        "/api/learner-context/v1/integrations/profile",
        headers={"Authorization": f"Bearer {one['access_token']}"},
    )
    assert revoked.status_code == 401
    still_valid = client.get(
        "/api/learner-context/v1/integrations/profile",
        headers={"Authorization": f"Bearer {two['access_token']}"},
    )
    assert still_valid.status_code == 200


def test_read_only_http_credential_cannot_record_interactions(tmp_path, monkeypatch):
    _isolate_users(monkeypatch, tmp_path)
    monkeypatch.setattr(routes, "AUTH_ENABLED", True)
    token = credentials.create("u_test", "read-only", 3, ("read",))
    client = TestClient(_integration_test_app())
    response = client.post(
        "/api/learner-context/v1/integrations/interactions",
        headers={"Authorization": f"Bearer {token['access_token']}"},
        json={"event_id": "e-1", "source": "pi", "user_message": "hello"},
    )
    assert response.status_code == 403
