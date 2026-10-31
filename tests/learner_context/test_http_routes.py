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
    from deeptutor.multi_user import paths
    monkeypatch.setattr(paths, "SYSTEM_ROOT", tmp_path / "system")
    monkeypatch.setattr(paths, "ADMIN_WORKSPACE_ROOT", tmp_path / "data")
    monkeypatch.setattr(paths, "USERS_ROOT", tmp_path / "data" / "users")
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
    monkeypatch.setattr(routes, "get_user_by_id", lambda user_id: (user_id, {"role": "user", "id": user_id}))


def test_disabled_auth_blocks_external_api_and_enabled_auth_requires_bearer(tmp_path, monkeypatch):
    _isolate_users(monkeypatch, tmp_path)
    from deeptutor.multi_user.context import reset_current_user, set_current_user
    from deeptutor.multi_user.paths import local_admin_user
    client = TestClient(_integration_test_app(), client=("127.0.0.1", 50000))
    local_context = set_current_user(local_admin_user())
    try:
        assert client.get("/api/learner-context/v1/integrations/profile").status_code == 401
    finally:
        reset_current_user(local_context)

    monkeypatch.setattr(routes, "AUTH_ENABLED", True)
    monkeypatch.setattr(routes, "get_user_by_id", lambda _user_id: ("u_test", {"id": "u_test", "role": "user"}))
    assert client.get("/api/learner-context/v1/integrations/profile").status_code == 401

    from deeptutor.multi_user.context import reset_current_user, set_current_user
    from deeptutor.multi_user.models import CurrentUser
    context = set_current_user(CurrentUser(id="u_test", username="u_test", role="user", scope=UserScope(kind="user", user_id="u_test", root=tmp_path / "u_test")))
    token = credentials.create("u_test", "read-only", 3, ("read",))
    token_value = credentials.load_token("u_test", token["id"])
    reset_current_user(context)
    owner_context = set_current_user(CurrentUser(id="local-admin", username="local", role="admin", scope=UserScope(kind="admin", user_id="local-admin", root=tmp_path / "data")))
    response = client.get(
        "/api/learner-context/v1/integrations/profile",
        headers={"Authorization": f"Bearer {token_value}"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["signals"] == 0
    reset_current_user(owner_context)


def test_http_profile_is_account_isolated_and_revocation_takes_effect(tmp_path, monkeypatch):
    _isolate_users(monkeypatch, tmp_path)
    monkeypatch.setattr(routes, "AUTH_ENABLED", True)
    from deeptutor.multi_user.context import reset_current_user, set_current_user
    from deeptutor.multi_user.models import CurrentUser
    token_values = {}
    for user_id, style in (("u_one", "tried_first"), ("u_two", "accepted")):
        account_context = set_current_user(CurrentUser(id=user_id, username=user_id, role="user", scope=UserScope(kind="user", user_id=user_id, root=tmp_path / user_id)))
        with routes._user_workspace(user_id):
            assert service.record_interaction_style(
                style=style, source="pi", event_id=f"{user_id}-event", quote="private quote"
            )

        created = credentials.create(user_id, "Pi" if user_id == "u_one" else "DSH", 3, ("read",))
        token_values[user_id] = credentials.load_token(user_id, created["id"])
        if user_id == "u_one":
            one = created
        else:
            two = created
        reset_current_user(account_context)
    client = TestClient(_integration_test_app(), client=("127.0.0.1", 50000))
    owner_context = set_current_user(CurrentUser(id="local-admin", username="local", role="admin", scope=UserScope(kind="admin", user_id="local-admin", root=tmp_path / "data")))
    monkeypatch.setattr(routes, "get_user_by_id", lambda user_id: (user_id, {"role": "user", "id": user_id}))
    first_response = client.get(
        "/api/learner-context/v1/integrations/profile",
        headers={"Authorization": f"Bearer {token_values['u_one']}"},
    )
    second_response = client.get(
        "/api/learner-context/v1/integrations/profile",
        headers={"Authorization": f"Bearer {token_values['u_two']}"},
    )
    assert first_response.status_code == second_response.status_code == 200
    first, second = first_response.json(), second_response.json()
    assert (first["signals"], first["shares"]) == (1, {"tried_first": 1.0})
    assert (second["signals"], second["shares"]) == (1, {"accepted": 1.0})
    assert first["records"] == second["records"] == []
    assert "private quote" not in str(first) + str(second)

    revoke_context = set_current_user(CurrentUser(id="u_one", username="u_one", role="user", scope=UserScope(kind="user", user_id="u_one", root=tmp_path / "u_one")))
    assert credentials.revoke("u_one", one["id"])
    reset_current_user(revoke_context)
    revoked = client.get(
        "/api/learner-context/v1/integrations/profile",
        headers={"Authorization": f"Bearer {token_values['u_one']}"},
    )
    assert revoked.status_code == 401
    still_valid = client.get(
        "/api/learner-context/v1/integrations/profile",
        headers={"Authorization": f"Bearer {token_values['u_two']}"}
    )
    assert still_valid.status_code == 200
    reset_current_user(owner_context)


def test_write_credential_does_not_send_text_to_provider_without_owner_opt_in(tmp_path, monkeypatch):
    _isolate_users(monkeypatch, tmp_path)
    monkeypatch.setattr(routes, "AUTH_ENABLED", True)
    monkeypatch.setattr(routes, "get_user_by_id", lambda _user_id: ("u_test", {"id": "u_test", "role": "user"}))
    from deeptutor.multi_user.context import reset_current_user, set_current_user
    from deeptutor.multi_user.models import CurrentUser
    context = set_current_user(CurrentUser(id="u_test", username="u_test", role="user", scope=UserScope(kind="user", user_id="u_test", root=tmp_path / "u_test")))
    token = credentials.create("u_test", "write", 3, ("read", "write"))
    token_value = credentials.load_token("u_test", token["id"])
    reset_current_user(context)
    from deeptutor.multi_user.paths import local_admin_user
    token_context = set_current_user(local_admin_user())
    import deeptutor.services.chat_mastery as chat_mastery
    monkeypatch.setattr(chat_mastery, "_style_collection_enabled", lambda: False)
    from deeptutor.learner_context import classifier
    monkeypatch.setattr(classifier, "classify_interaction", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("must not call provider")))

    response = TestClient(_integration_test_app(), client=("127.0.0.1", 50000)).post(
        "/api/learner-context/v1/integrations/interactions",
        headers={"Authorization": f"Bearer {token_value}"},
        json={"event_id": "e-no-consent", "source": "pi", "user_message": "synthetic learner text"},
    )
    assert response.status_code == 202
    assert response.json() == {"accepted": False, "reason": "classification_not_enabled"}
    reset_current_user(token_context)


def test_settings_api_reads_current_account_style_flags(tmp_path, monkeypatch):
    monkeypatch.setattr(routes, "AUTH_ENABLED", True)
    calls = []

    def profile_for_path(**kwargs):
        calls.append(kwargs)
        return {"enabled": True, "signals": 7, "automatic_collection_enabled": True}

    monkeypatch.setattr(routes.service, "get_profile", profile_for_path)
    monkeypatch.setattr(
        routes,
        "_chat_mastery_module",
        lambda: type("ChatMastery", (), {"_style_collection_enabled": staticmethod(lambda: True)})(),
    )
    from deeptutor.multi_user.context import set_current_user, reset_current_user
    from deeptutor.multi_user.models import CurrentUser, UserScope
    user_id = "u_settings"
    root = tmp_path / user_id
    scope = UserScope(kind="user", user_id=user_id, root=root)
    token = set_current_user(CurrentUser(id=user_id, username=user_id, role="user", scope=scope))
    try:
        from starlette.responses import Response as StarletteResponse

        result = routes.get_style_settings(StarletteResponse())
        assert result == {"enabled": True, "signals": 7, "automatic_collection_enabled": True}
        assert calls == [{"path": root / "user" / "chat_mastery.sqlite3"}]
    finally:
        reset_current_user(token)


def test_read_only_http_credential_cannot_record_interactions(tmp_path, monkeypatch):
    _isolate_users(monkeypatch, tmp_path)
    monkeypatch.setattr(routes, "AUTH_ENABLED", True)
    monkeypatch.setattr(routes, "get_user_by_id", lambda _user_id: ("u_test", {"id": "u_test", "role": "user"}))
    from deeptutor.multi_user.context import reset_current_user, set_current_user
    from deeptutor.multi_user.models import CurrentUser
    monkeypatch.setattr(routes, "get_user_by_id", lambda _user_id: ("u_test", {"id": "u_test", "role": "user"}))
    context = set_current_user(CurrentUser(id="u_test", username="u_test", role="user", scope=UserScope(kind="user", user_id="u_test", root=tmp_path / "u_test")))
    token = credentials.create("u_test", "read-only", 3, ("read",))
    token_value = credentials.load_token("u_test", token["id"])
    reset_current_user(context)
    client = TestClient(_integration_test_app(), client=("127.0.0.1", 50000))
    response = client.post(
        "/api/learner-context/v1/integrations/interactions",
        headers={"Authorization": f"Bearer {token_value}"},
        json={"event_id": "e-1", "source": "pi", "user_message": "hello"},
    )
    assert response.status_code == 403
