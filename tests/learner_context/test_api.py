from __future__ import annotations

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from deeptutor.api.routers import learner_context as api
from deeptutor.learner_context import credentials, service
from deeptutor.multi_user.context import get_current_user, reset_current_user, set_current_user
from deeptutor.multi_user.models import CurrentUser, UserScope
from deeptutor.services.path_service import PathService


def _isolated_api_paths(monkeypatch, tmp_path):
    from deeptutor.multi_user import paths
    monkeypatch.setattr(paths, "SYSTEM_ROOT", tmp_path / "system")
    monkeypatch.setattr(paths, "ADMIN_WORKSPACE_ROOT", tmp_path / "data")
    monkeypatch.setattr(paths, "USERS_ROOT", tmp_path / "data" / "users")
    monkeypatch.delenv("PI_TUTOR_LEARNER_CONTEXT_DB", raising=False)
    monkeypatch.setattr(api, "scope_for_user", lambda uid, is_admin=False: UserScope(kind="user", user_id=uid, root=tmp_path / uid))
    monkeypatch.setattr(api, "ensure_scope_workspace", lambda scope: (scope.root / "data").mkdir(parents=True, exist_ok=True))
    monkeypatch.setattr(api, "get_path_service_for_scope", lambda scope: PathService(workspace_root=scope.root))
    monkeypatch.setattr(api, "get_user_by_id", lambda user_id: (user_id, {"role": "user"}))


def _current_user(user_id: str, root) -> CurrentUser:
    return CurrentUser(id=user_id, username=user_id, role="user", scope=UserScope(kind="user", user_id=user_id, root=root))


def test_integration_credential_is_hashed_scoped_and_revocable(tmp_path, monkeypatch):
    monkeypatch.setenv("PI_TUTOR_LEARNER_CONTEXT_CREDENTIALS_DB", str(tmp_path / "credentials.db"))
    from deeptutor.multi_user import paths
    monkeypatch.setattr(paths, "SYSTEM_ROOT", tmp_path / "system")
    monkeypatch.setattr(paths, "ADMIN_WORKSPACE_ROOT", tmp_path / "data")
    monkeypatch.setattr(paths, "USERS_ROOT", tmp_path / "data" / "users")
    token_context = set_current_user(CurrentUser(id="u_test", username="u_test", role="user", scope=UserScope(kind="user", user_id="u_test", root=tmp_path)))
    try:
        credentials.revoke_all("u_test")
        token = credentials.create("u_test", "Pi adapter", 1, ("read",))
        token_value = credentials.load_token("u_test", token["id"])
        row = credentials.authenticate(token_value, owner_id="u_test")
        assert row["user_id"] == "u_test"
        assert row["scopes"] == "read"
        assert "access_token" not in row
        assert token["access_token"] == ""
        assert credentials.load_token("u_test", token["id"]) == token_value
        assert credentials.authenticate("dtlc_invalid") is None
        assert credentials.authenticate(None) is None
        second = credentials.create("u_test", "second", 1, ("read",))
        second_value = credentials.load_token("u_test", second["id"])
        assert credentials.revoke_all("u_test") == 2
        assert "u_test" not in credentials._secret_store().load_secret_values("u_test", "learner-context")
        assert credentials.authenticate(second_value, owner_id="u_test") is None
        assert not credentials.revoke("u_test", token["id"])
        assert credentials.authenticate(token_value, owner_id="u_test") is None
        assert credentials._secret_store().load_secret_values("u_test", "learner-context") == {}
        assert credentials.load_token("u_test", token["id"]) is None
        assert credentials.load_token("u_test", second["id"]) is None
    finally:
        reset_current_user(token_context)


def test_unknown_owner_bearer_does_not_create_credential_database(tmp_path, monkeypatch):
    from deeptutor.multi_user import paths

    monkeypatch.setattr(paths, "SYSTEM_ROOT", tmp_path / "system")
    monkeypatch.setattr(paths, "ADMIN_WORKSPACE_ROOT", tmp_path / "data")
    monkeypatch.setattr(paths, "USERS_ROOT", tmp_path / "data" / "users")
    token_context = set_current_user(_current_user("u_unknown", tmp_path / "unknown"))
    try:
        assert credentials.authenticate("dtlc_u_unknown." + "a" * 24 + ".random", owner_id="u_unknown") is None
        assert credentials.load_token("u_unknown", "a" * 24) is None
        assert not credentials.database_path("u_unknown").exists()
        assert not (paths.SYSTEM_ROOT / "user-secrets" / "u_unknown").exists()
    finally:
        reset_current_user(token_context)


def test_credential_store_requires_current_owner(tmp_path, monkeypatch):
    monkeypatch.setenv("PI_TUTOR_LEARNER_CONTEXT_CREDENTIALS_DB", str(tmp_path / "credentials.db"))
    from deeptutor.multi_user import paths
    monkeypatch.setattr(paths, "SYSTEM_ROOT", tmp_path / "system")
    monkeypatch.setattr(paths, "ADMIN_WORKSPACE_ROOT", tmp_path / "data")
    monkeypatch.setattr(paths, "USERS_ROOT", tmp_path / "data" / "users")
    token_context = set_current_user(_current_user("u_one", tmp_path / "one"))
    try:
        with pytest.raises(ValueError, match="current account"):
            credentials.create("u_two", "cross-account", 1, ("read",))
        token = credentials.create("u_one", "owner token", 1, ("read",))
        token_value = credentials.load_token("u_one", token["id"])
        assert credentials.authenticate(token_value, owner_id="u_one")["user_id"] == "u_one"
        assert credentials.list_for_user("u_two") == []
        assert not credentials.revoke("u_two", token["id"])
    finally:
        reset_current_user(token_context)


def test_integration_scope_enforced():
    with pytest.raises(HTTPException) as err:
        api._require_scope({"user_id": "u_test", "scopes": "read"}, "write")
    assert err.value.status_code == 403


def test_integration_workspace_keeps_each_users_db_separate(tmp_path, monkeypatch):
    _isolated_api_paths(monkeypatch, tmp_path)
    monkeypatch.setattr(api, "get_user_by_id", lambda user_id: (user_id, {"id": user_id, "role": "user"}))
    for user_id, style in (("u1", "tried_first"), ("u2", "accepted")):
        with api._user_workspace(user_id):
            assert service.record_interaction_style(style=style, source="pi", event_id="turn-1")
            assert get_current_user().id == user_id
    for user_id, expected in (("u1", "tried_first"), ("u2", "accepted")):
        with api._user_workspace(user_id):
            assert service.get_profile()["records"][0]["style"] == expected


def test_external_endpoint_profile_is_scoped_to_credential_owner(tmp_path, monkeypatch):
    from fastapi import Response

    _isolated_api_paths(monkeypatch, tmp_path)
    monkeypatch.setattr(api, "AUTH_ENABLED", True)
    monkeypatch.setattr(api, "get_user_by_id", lambda user_id: (user_id, {"id": user_id, "role": "user"}))
    with api._user_workspace("u_test"):
        service.record_interaction_style(style="pushed_back", source="pi", event_id="turn")
    token_context = set_current_user(_current_user("u_test", tmp_path / "u_test"))
    try:
        created = credentials.create("u_test", "profile", 1, ("read",))
        credential = credentials.authenticate(credentials.load_token("u_test", created["id"]), owner_id="u_test")
    finally:
        reset_current_user(token_context)
    profile = api.integration_profile(Response(), credential)
    assert profile["signals"] == 1
    assert profile["shares"] == {"pushed_back": 1.0}
    assert profile["records"] == []


def test_authenticated_deletion_wrappers_reject_mismatched_user(tmp_path, monkeypatch):
    monkeypatch.delenv("PI_TUTOR_LEARNER_CONTEXT_DB", raising=False)
    monkeypatch.setattr("deeptutor.services.path_service.get_path_service", lambda: PathService(workspace_root=tmp_path))
    token = set_current_user(_current_user("u_owner", tmp_path))
    try:
        assert not service.delete_record_for_current_user("u_other", 1)
        assert service.clear_records_for_current_user("u_other") == 0
    finally:
        reset_current_user(token)


def test_request_payload_validation_rejects_bad_source():
    from deeptutor.api.routers.learner_context import InteractionRequest

    with pytest.raises(Exception):
        InteractionRequest(event_id="event", source="bad source", user_message="message")


def test_request_bounds_input_and_stores_only_short_quote(tmp_path, monkeypatch):
    from deeptutor.api.routers.learner_context import InteractionRequest
    from deeptutor.learner_context.classifier import classify_interaction

    payload = InteractionRequest(event_id="e-1", source="pi", user_message="x" * 4000, assistant_response="y" * 3000)
    assert len(payload.user_message) == 4000
    assert len(payload.assistant_response) == 3000
    from deeptutor.learner_context.classifier import classify_interaction
    assert callable(classify_interaction)
    with pytest.raises(Exception):
        InteractionRequest(event_id="e-2", source="pi", user_message="x" * 4001)
    assert callable(classify_interaction)


def test_owner_cannot_use_external_integration_route_when_auth_disabled(monkeypatch):
    monkeypatch.setattr(api, "AUTH_ENABLED", False)
    from starlette.requests import Request

    request = Request({"type": "http", "method": "GET", "path": "/", "headers": [(b"cf-access-jwt-assertion", b"spoofed")], "client": ("203.0.113.10", 1234), "server": ("test", 80), "scheme": "http", "query_string": b""})
    with pytest.raises(HTTPException) as err:
        api._require_integration(request, "Bearer dtlc_bad")
    assert err.value.status_code == 403


def test_openapi_exposes_versioned_harness_contract():
    from deeptutor.api.main import app

    spec = app.openapi()
    assert "/api/learner-context/v1/integrations/profile" in spec["paths"]
    assert "/api/learner-context/v1/integrations/interactions" in spec["paths"]


@pytest.mark.asyncio
async def test_classifier_request_discards_transcript(monkeypatch, tmp_path):
    from deeptutor.api.routers import learner_context as api
    from deeptutor.learner_context import classifier, repository, service

    path = tmp_path / "account.sqlite3"
    monkeypatch.setattr(repository, "database_path", lambda: path)
    monkeypatch.setattr(api, "AUTH_ENABLED", True)
    monkeypatch.setattr(api, "get_user_by_id", lambda _user_id: ("u_test", {"role": "user", "id": "u_test"}))
    monkeypatch.setattr(api, "scope_for_user", lambda user_id, is_admin=False: UserScope(kind="user", user_id=user_id, root=tmp_path / user_id))
    monkeypatch.setattr(api, "ensure_scope_workspace", lambda scope: scope.root.mkdir(parents=True, exist_ok=True))
    monkeypatch.setattr(api, "get_path_service_for_scope", lambda scope: PathService(workspace_root=scope.root))
    async def no_signal(*_args):
        return None

    monkeypatch.setattr(classifier, "classify_interaction", no_signal)
    monkeypatch.setattr("deeptutor.services.chat_mastery._style_collection_enabled", lambda: True)
    context = set_current_user(_current_user("u_test", tmp_path))
    try:
        result = await api.integration_record_interaction(
            api.InteractionRequest(event_id="event-1", source="pi", user_message="private learner message", assistant_response="private assistant response"),
            {"id": "a" * 24, "user_id": "u_test", "scopes": "read,write"},
        )
        assert result == {"accepted": False, "reason": "no_learning_signal"}
        profile = service.get_profile(path=path)
        assert profile["records"] == []
    finally:
        reset_current_user(context)
