"""Tests for LLM error mapping helpers."""

from datetime import datetime, timezone

import pytest

from deeptutor.services.llm.error_mapping import (
    parse_retry_after_seconds,
    retry_after_seconds,
)
from deeptutor.services.llm.exceptions import (
    LLMAPIError,
    LLMAuthenticationError,
    LLMProviderTransportError,
    LLMRateLimitError,
    ProviderContextWindowError,
)


class DummyError(Exception):
    """Custom error used for mapping tests."""

    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


def test_retry_after_seconds_parses_http_date() -> None:
    error = DummyError("temporarily unavailable", status_code=503)
    error.response = type(
        "Response",
        (),
        {"headers": {"Retry-After": "Wed, 21 Oct 2015 07:28:10 GMT"}},
    )()

    delay = retry_after_seconds(
        error,
        now=datetime(2015, 10, 21, 7, 28, tzinfo=timezone.utc),
    )

    assert delay == 10.0


@pytest.mark.parametrize("value", ["", "tomorrow", "-1", "nan", True, None])
def test_parse_retry_after_seconds_rejects_invalid_values(value: object) -> None:
    assert parse_retry_after_seconds(value) is None
