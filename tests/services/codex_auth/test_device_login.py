"""The ChatGPT device-code sign-in runs on pi's OAuth flow and lands in the same credential store."""

from __future__ import annotations

import asyncio
from pathlib import Path
import shutil

import pytest

from deeptutor.services.codex_auth.contracts import CodexAuthError
from deeptutor.services.codex_auth.pi_login import DeviceLogin, payload_from_credential
from tests.services.codex_auth.test_service import _oauth_service

STUB = Path(__file__).parent / "fixtures" / "openai_stub.ts"
ENGINE = Path(__file__).resolve().parents[3] / "pi_engine"

needs_engine = pytest.mark.skipif(
    shutil.which("bun") is None or not (ENGINE / "node_modules").exists(),
    reason="the pi engine is not installed",
)


TERMINAL = {"completed", "cancelled", "expired", "failed"}


async def _terminal(service) -> dict:
    for _ in range(600):  # the sign-in runs in another process, so give it real time
        status = service.public_status()
        if status["operation_state"] in TERMINAL:
            return status
        await asyncio.sleep(0.05)
    raise AssertionError("device sign-in did not finish")


@pytest.fixture(autouse=True)
def stubbed_openai(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PI_TUTOR_AUTH_PRELOAD", str(STUB))


def test_pi_credential_becomes_the_token_payload_the_service_commits() -> None:
    payload = payload_from_credential(
        {"access": "a", "refresh": "r", "expires": 1_700_000_000_000, "accountId": "acct"}
    )
    assert payload == {
        "access_token": "a",
        "refresh_token": "r",
        "id_token": "a",
        "account_id": "acct",
        "expires_at": 1_700_000_000,
    }
    with pytest.raises(CodexAuthError):
        payload_from_credential({"access": "", "refresh": "r"})


@needs_engine
@pytest.mark.asyncio
async def test_device_login_shows_a_code_then_returns_the_tokens() -> None:
    login = DeviceLogin()
    await login.start()
    assert login.user_code == "ABCD-1234"
    assert login.verification_uri == "https://auth.openai.com/codex/device"
    payload = await login.wait()
    assert payload["account_id"] == "acct-9" and payload["refresh_token"] == "r1"
    assert payload["expires_at"] > 0


@needs_engine
@pytest.mark.asyncio
async def test_cancelling_a_device_login_stops_the_engine_process() -> None:
    login = DeviceLogin()
    await login.start()
    proc = login._proc
    await login.cancel()
    assert proc is not None and proc.returncode is not None


@needs_engine
@pytest.mark.asyncio
async def test_service_device_login_commits_the_account_like_the_browser_flow(tmp_path: Path) -> None:
    service, _callback, _oauth, _catalog, store, _model_catalog = await _oauth_service(tmp_path)

    started = await service.start_device_login()
    assert started["login_method"] == "device" and started["user_code"] == "ABCD-1234"
    status = service.public_status()
    assert status["connection"] == "authorizing" and status["user_code"] == "ABCD-1234"

    done = await _terminal(service)
    assert done["operation_state"] == "completed"
    credentials = store.load_credentials()
    assert credentials is not None and credentials.account_id == "acct-9"
    assert service.public_status()["connection"] == "connected"


@needs_engine
@pytest.mark.asyncio
async def test_service_device_login_can_be_cancelled(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PI_TUTOR_AUTH_PRELOAD")  # nothing answers, so the sign-in stays pending
    service, *_ = await _oauth_service(tmp_path)
    monkeypatch.setenv("PI_TUTOR_AUTH_PRELOAD", str(STUB.with_name("openai_pending_stub.ts")))
    STUB.with_name("openai_pending_stub.ts").write_text(
        STUB.read_text().replace("++polls < 2", "true"), encoding="utf-8"
    )
    try:
        await service.start_device_login()
        status = await service.cancel_login()
        assert status["connection"] == "disconnected"
        assert (await _terminal(service))["operation_state"] == "cancelled"
    finally:
        STUB.with_name("openai_pending_stub.ts").unlink(missing_ok=True)
