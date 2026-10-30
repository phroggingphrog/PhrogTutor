"""Small HTTP client for harness adapters outside the Pi Tutor process."""

from __future__ import annotations

import os

import httpx


class LearnerContextClient:
    """Client for read-only profile access and optional interaction recording."""

    def __init__(
        self,
        base_url: str | None = None,
        token: str | None = None,
        *,
        timeout: float = 65,
        transport: httpx.BaseTransport | None = None,
    ):
        self.base_url = (base_url or os.environ.get("PI_TUTOR_LEARNER_CONTEXT_URL", "")).rstrip("/")
        self.token = token or os.environ.get("PI_TUTOR_LEARNER_CONTEXT_TOKEN", "")
        if not self.base_url or not self.token:
            raise ValueError("Set the learner-context base URL and integration token.")
        self._client = httpx.Client(
            timeout=timeout,
            headers={"Authorization": f"Bearer {self.token}"},
            transport=transport,
            follow_redirects=False,
        )

    def profile(self) -> dict:
        return self._get("/profile")

    def prompt_context(self) -> str:
        return str(self._get("/prompt-context").get("prompt_context") or "")

    def record_interaction(
        self,
        event_id: str,
        source: str,
        user_message: str,
        assistant_response: str = "",
    ) -> dict:
        response = self._client.post(
            f"{self.base_url}/api/learner-context/v1/integrations/interactions",
            json={
                "event_id": event_id,
                "source": source,
                "user_message": user_message,
                "assistant_response": assistant_response,
            },
        )
        response.raise_for_status()
        return response.json()

    def _get(self, path: str) -> dict:
        response = self._client.get(f"{self.base_url}/api/learner-context/v1/integrations{path}")
        response.raise_for_status()
        return response.json()

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "LearnerContextClient":
        return self

    def __exit__(self, *_exc) -> None:
        self.close()
