from __future__ import annotations

from fastapi import FastAPI

from deeptutor.learner_context.client import LearnerContextClient
from deeptutor.api.routers import learner_context


def test_python_client_reads_profile_and_records_events(monkeypatch, tmp_path):
    monkeypatch.setattr(learner_context, "AUTH_ENABLED", True)
    monkeypatch.setenv("PI_TUTOR_LEARNER_CONTEXT_CREDENTIALS_DB", str(tmp_path / "tokens.db"))
    credential = learner_context.credentials.create("u_client", "test", 3, ("read", "write"))
    app = FastAPI()
    app.include_router(learner_context.integration_router, prefix="/api/learner-context/v1/integrations")
    transport = __import__("httpx").MockTransport(
        lambda request: __import__("httpx").Response(200, json={"schema_version": 1, "enabled": True, "signals": 0, "shares": {}, "dominant": None, "instruction": "", "records": []})
    )
    client = LearnerContextClient("http://test", credential["access_token"], transport=transport)
    assert client.profile()["schema_version"] == 1
    assert client.prompt_context() == ""
    client.close()
