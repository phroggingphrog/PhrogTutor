from __future__ import annotations

from fastapi import FastAPI

from deeptutor.learner_context.client import LearnerContextClient
from deeptutor.api.routers import learner_context


def test_multi_account_client_requires_and_uses_owner_bearer(monkeypatch):
    monkeypatch.setattr("deeptutor.services.auth.AUTH_ENABLED", True)
    monkeypatch.setenv("PI_TUTOR_AUTH_TOKEN", "owner-bearer")
    import httpx

    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(200, json={"prompt_context": "ok"})

    client = LearnerContextClient("https://deeptutor.example", transport=httpx.MockTransport(handler))
    try:
        assert client.prompt_context() == "ok"
        assert requests[0].headers["authorization"] == "Bearer owner-bearer"
        assert requests[0].url.path == "/api/learner-context/v1/prompt-context"
    finally:
        client.close()


def test_multi_account_mcp_uses_owner_token(monkeypatch):
    monkeypatch.setattr("deeptutor.services.auth.AUTH_ENABLED", True)
    monkeypatch.setenv("PI_TUTOR_AUTH_TOKEN", "owner-bearer")
    monkeypatch.setenv("PI_TUTOR_LEARNER_CONTEXT_URL", "https://deeptutor.example")
    monkeypatch.delenv("PI_TUTOR_LEARNER_CONTEXT_TOKEN_ID", raising=False)
    monkeypatch.setenv("PI_TUTOR_LEARNER_CONTEXT_URL", "https://deeptutor.example")
    from deeptutor.learner_context import mcp_server

    assert mcp_server._integration_token() == "owner-bearer"
    import httpx

    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(200, json={"prompt_context": "ok"})

    client_class = httpx.Client
    monkeypatch.setattr(
        mcp_server,
        "httpx",
        httpx,
        raising=False,
    )
    monkeypatch.setattr(
        httpx,
        "Client",
        lambda **kwargs: client_class(
            transport=httpx.MockTransport(handler),
            **kwargs,
        ),
    )
    assert mcp_server._request("GET", "/prompt-context")["prompt_context"] == "ok"
    assert requests[0].headers["authorization"] == "Bearer owner-bearer"
    assert requests[0].url.path == "/api/learner-context/v1/prompt-context"


def test_python_client_reads_profile_and_records_events(monkeypatch, tmp_path):
    monkeypatch.setattr(learner_context, "AUTH_ENABLED", False)
    from deeptutor.multi_user import paths
    monkeypatch.setattr(paths, "SYSTEM_ROOT", tmp_path / "system")
    monkeypatch.setattr(paths, "ADMIN_WORKSPACE_ROOT", tmp_path / "data")
    monkeypatch.setattr(paths, "USERS_ROOT", tmp_path / "data" / "users")
    monkeypatch.setenv("PI_TUTOR_LEARNER_CONTEXT_CREDENTIALS_DB", str(tmp_path / "tokens.db"))
    from deeptutor.multi_user.context import reset_current_user, set_current_user
    from deeptutor.multi_user.models import CurrentUser, UserScope
    token = set_current_user(CurrentUser(id="u_client", username="u_client", role="user", scope=UserScope(kind="user", user_id="u_client", root=tmp_path / "data" / "users" / "u_client")))
    credential = learner_context.credentials.create("u_client", "test", 3, ("read", "write"))
    reset_current_user(token)
    app = FastAPI()
    app.include_router(learner_context.integration_router, prefix="/api/learner-context/v1/integrations")
    monkeypatch.setenv("PI_TUTOR_LEARNER_CONTEXT_TOKEN_ID", credential["id"])
    transport = __import__("httpx").MockTransport(
        lambda request: __import__("httpx").Response(200, json={"schema_version": 1, "enabled": True, "signals": 0, "shares": {}, "dominant": None, "instruction": "", "records": []})
    )
    token = set_current_user(CurrentUser(id="u_client", username="u_client", role="user", scope=UserScope(kind="user", user_id="u_client", root=tmp_path / "data" / "users" / "u_client")))
    client = LearnerContextClient("http://127.0.0.1:18051", transport=transport)
    assert client._client.timeout.read == 65.0
    reset_current_user(token)
    assert client.profile()["schema_version"] == 1
    assert client.prompt_context() == ""
    client.close()
