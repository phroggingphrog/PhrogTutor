"""Small Python client for the learner-context integration API."""

from __future__ import annotations

import os
from urllib.parse import urlsplit

import httpx

from . import credentials


class LearnerContextClient:
    def __init__(
        self,
        base_url: str | None = None,
        token: str | None = None,
        *,
        timeout: float = 65.0,
        transport: httpx.BaseTransport | None = None,
    ):
        parsed = urlsplit(base_url or os.environ.get("PI_TUTOR_LEARNER_CONTEXT_URL", ""))
        loopback_http = parsed.scheme == "http" and parsed.hostname in {"127.0.0.1", "localhost", "::1"}
        if parsed.scheme != "https" and not loopback_http:
            raise ValueError("Learner-context integrations require loopback HTTP or HTTPS.")
        self.base_url = (base_url or os.environ.get("PI_TUTOR_LEARNER_CONTEXT_URL", "")).rstrip("/")
        owner = credentials.current_owner_id()
        from deeptutor.services.auth import AUTH_ENABLED
        if AUTH_ENABLED and token is None:
            token = os.environ.get("PI_TUTOR_AUTH_TOKEN")
        if AUTH_ENABLED and token is None:
            raise ValueError("Multi-account clients must pass a signed-in owner bearer token.")
        token_id = os.environ.get("PI_TUTOR_LEARNER_CONTEXT_TOKEN_ID", "")
        local_token = credentials.load_token(owner, token_id) if token_id and not AUTH_ENABLED else None
        self.token = token or local_token or ""
        if not self.base_url or not self.token:
            if AUTH_ENABLED:
                raise ValueError("Set the learner-context URL and signed-in owner bearer token.")
            raise ValueError("Set the learner-context URL and local secret-store credential ID.")
        self._client = httpx.Client(
            timeout=timeout,
            transport=transport,
            headers={"Authorization": f"Bearer {self.token}"},
        )
        self._owner_id = owner

    def profile(self) -> dict:
        response = self._request("GET", "/profile")
        return response.json()

    def prompt_context(self) -> str:
        response = self._request("GET", "/prompt-context")
        return str(response.json().get("prompt_context") or "")

    def record_interaction(
        self,
        *,
        event_id: str,
        source: str,
        user_message: str,
        assistant_response: str = "",
    ) -> dict:
        response = self._request(
            "POST",
            "/interactions",
            json={
                "event_id": event_id,
                "source": source,
                "user_message": user_message,
                "assistant_response": assistant_response,
            },
        )
        response.raise_for_status()
        return response.json()

    def _request(self, method: str, path: str, **kwargs):
        from deeptutor.services.auth import AUTH_ENABLED

        endpoint = "/api/learner-context/v1" if AUTH_ENABLED else "/api/learner-context/v1/integrations"
        response = self._client.request(method, f"{self.base_url}{endpoint}{path}", **kwargs)
        response.raise_for_status()
        return response

    def close(self) -> None:
        self._client.close()

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.close()
