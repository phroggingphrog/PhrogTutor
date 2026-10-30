from __future__ import annotations

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from deeptutor.api.routers import learner_context as api
from deeptutor.learner_context import credentials, service
from deeptutor.multi_user.context import get_current_user
from deeptutor.services.path_service import PathService
from deeptutor.multi_user.models import UserScope


def _isolated_api_paths(monkeypatch, tmp_path):
    monkeypatch.delenv("PI_TUTOR_LEARNER_CONTEXT_DB", raising=False)
    monkeypatch.setattr(api, "scope_for_user", lambda uid, is_admin=False: UserScope(kind="user", user_id=uid, root=tmp_path / uid))
    monkeypatch.setattr(api, "ensure_scope_workspace", lambda scope: (scope.root / "data").mkdir(parents=True, exist_ok=True))
    monkeypatch.setattr(api, "get_path_service_for_scope", lambda scope: PathService(workspace_root=scope.root))
    monkeypatch.setattr(api, "get_user_by_id", lambda user_id: (user_id, {"role": "user"}))


def test_integration_credential_is_hashed_scoped_and_revocable(tmp_path, monkeypatch):
    monkeypatch.setenv("PI_TUTOR_LEARNER_CONTEXT_CREDENTIALS_DB", str(tmp_path / "credentials.db"))
    credentials.revoke_all("u_test")
    token = credentials.create("u_test", "Pi adapter", 1, ("read",))
    row = credentials.authenticate(token["access_token"])
    assert row["user_id"] == "u_test"
    assert row["scopes"] == "read"
    assert "access_token" not in row
    assert credentials.authenticate("dtlc_invalid") is None
    assert credentials.authenticate(None) is None
    second = credentials.create("u_test", "second", 1, ("read",))
    assert credentials.revoke_all("u_test") == 2
    assert credentials.authenticate(second["access_token"]) is None
    assert not credentials.revoke("u_test", token["id"])
    assert credentials.authenticate(token["access_token"]) is None


def test_integration_scope_enforced():
    with pytest.raises(HTTPException) as err:
        api._require_scope({"user_id": "u_test", "scopes": "read"}, "write")
    assert err.value.status_code == 403


def test_integration_workspace_keeps_each_users_db_separate(tmp_path, monkeypatch):
    _isolated_api_paths(monkeypatch, tmp_path)
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
    with api._user_workspace("u_test"):
        service.record_interaction_style(style="pushed_back", source="pi", event_id="event")
    integration = api.credentials
    token = integration.create("u_test", "Pi", 1, ("read",))
    credential = integration.authenticate(token["access_token"])
    assert credential
    profile = api.integration_profile(Response(), credential)
    assert profile["signals"] == 1
    assert profile["records"] == []
    assert profile["dominant"] is None


def test_authenticated_deletion_wrappers_reject_mismatched_user(tmp_path, monkeypatch):
    monkeypatch.delenv("PI_TUTOR_LEARNER_CONTEXT_DB", raising=False)
    monkeypatch.setattr("deeptutor.services.path_service.get_path_service", lambda: PathService(workspace_root=tmp_path))
    from deeptutor.multi_user.context import set_current_user
    from deeptutor.multi_user.models import UserScope, CurrentUser
    user_token = set_current_user(CurrentUser(id="u_owner", username="owner", role="user", scope=UserScope(kind="user", user_id="u_owner", root=tmp_path)))
    try:
        assert not service.delete_record_for_current_user("u_other", 1)
        assert service.clear_records_for_current_user("u_other") == 0
    finally:
        from deeptutor.multi_user.context import reset_current_user
        reset_current_user(user_token)


def test_request_payload_validation_rejects_bad_source():
    with pytest.raises(Exception):
        api.InteractionRequest(event_id="event-1", source="bad source", user_message="hello")


def test_request_bounds_input_and_stores_only_short_quote(tmp_path, monkeypatch):
    monkeypatch.delenv("PI_TUTOR_LEARNER_CONTEXT_DB", raising=False)
    monkeypatch.setattr("deeptutor.multi_user.paths.get_account_path_service", lambda: PathService(workspace_root=tmp_path))
    assert service.record_interaction_style(style="tried_first", source="pi", event_id="event", quote="x" * 500)
    assert len(service.get_profile()["records"][0]["quote"]) == 200


def test_owner_cannot_use_external_integration_route_when_auth_disabled(monkeypatch):
    monkeypatch.setattr(api, "AUTH_ENABLED", False)
    with pytest.raises(HTTPException) as err:
        api._require_integration("Bearer dtlc_bad")
    assert err.value.status_code == 503


def test_openapi_exposes_versioned_harness_contract():
    from deeptutor.api.main import app

    paths = app.openapi()["paths"]
    assert "/api/learner-context/v1/profile" in paths
    assert "/api/learner-context/v1/integrations/profile" in paths
    assert "/api/learner-context/v1/integrations/interactions" in paths


def test_classifier_request_discards_transcript(monkeypatch, tmp_path):
    from deeptutor.api.routers import learner_context
    from deeptutor.learner_context import classifier
    _isolated_api_paths(monkeypatch, tmp_path)
    monkeypatch.setenv("PI_TUTOR_LEARNER_CONTEXT_CREDENTIALS_DB", str(tmp_path / "tokens.db"))
    token = credentials.create("u_test", "Pi", 1, ("read", "write"))
    credential = credentials.authenticate(token["access_token"])
    assert credential
    monkeypatch.setattr(learner_context, "AUTH_ENABLED", True)
    monkeypatch.setattr(classifier, "classify_interaction", lambda *_args: _return_style())

    async def call():
        return await learner_context.integration_record_interaction(
            learner_context.InteractionRequest(
                event_id="event-1", source="pi", user_message="design a lesson", assistant_response="temporary transcript"
            ),
            credential,
        )

    import asyncio
    result = asyncio.run(call())
    assert result == {"accepted": True, "duplicate": False}
    with api._user_workspace("u_test"):
        profile = service.get_profile()
    assert profile["records"][0]["quote"] == "design a lesson"
    assert "temporary transcript" not in str(profile)


async def _return_style():
    return "own_idea"
