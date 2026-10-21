"""ChatGPT device-code sign-in, run by pi's own OAuth flow (``pi_engine/auth.ts``).

A server cannot always receive the browser redirect the classic flow needs (Docker, a remote host).
The device-code flow only needs the user to enter a short code at OpenAI, so it works anywhere.
The protocol lives in pi-ai and is maintained upstream; this module only runs it and hands back
the tokens in the shape :class:`CodexOAuthService` stores.
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Any

from .contracts import CodexAuthError

_ENGINE = Path(__file__).resolve().parents[3] / "pi_engine"
_START_TIMEOUT_S = 30


def _command() -> list[str]:
    binary = os.path.expanduser(os.environ.get("PI_TUTOR_ENGINE_BIN", "bun"))
    extra = os.environ.get("PI_TUTOR_AUTH_PRELOAD", "").strip()  # tests stub the OpenAI endpoints with it
    return [binary, *(["--preload", extra] if extra else []), str(_ENGINE / "auth.ts")]


def payload_from_credential(credential: dict[str, Any]) -> dict[str, Any]:
    """pi's ``{access, refresh, expires(ms), accountId}`` as the token payload the service commits."""
    access = credential.get("access")
    refresh = credential.get("refresh")
    expires = credential.get("expires")
    if not (isinstance(access, str) and access and isinstance(refresh, str) and refresh):
        raise CodexAuthError("token_response_invalid", "Codex returned incomplete authentication data.", 502)
    payload: dict[str, Any] = {
        "access_token": access,
        "refresh_token": refresh,
        "id_token": access,  # pi's flow has no separate ID token; the access token carries the same account claims
        "account_id": credential.get("accountId"),
    }
    if isinstance(expires, (int, float)) and expires > 0:
        payload["expires_at"] = int(expires // 1000)
    return payload


class DeviceLogin:
    """One running sign-in: ``await start()`` for the code to show, ``await wait()`` for the tokens."""

    def __init__(self) -> None:
        self._proc: asyncio.subprocess.Process | None = None
        self.user_code = ""
        self.verification_uri = ""
        self.expires_in = 0

    async def start(self) -> None:
        try:
            self._proc = await asyncio.create_subprocess_exec(
                *_command(),
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
        except OSError as exc:
            raise CodexAuthError(
                "login_unavailable", "Device sign-in needs the pi engine (bun) to be installed.", 503
            ) from exc
        assert self._proc.stdin is not None
        self._proc.stdin.write(b'{"op":"device_login"}\n')
        await self._proc.stdin.drain()
        try:
            message = await asyncio.wait_for(self._read(), _START_TIMEOUT_S)
        except TimeoutError:
            await self.cancel()
            raise CodexAuthError("login_failed", "OpenAI did not answer the sign-in request.", 502) from None
        if message.get("event") != "device_code":
            await self.cancel()
            raise CodexAuthError("login_failed", "Codex sign-in could not be started.", 502)
        self.user_code = str(message["userCode"])
        self.verification_uri = str(message["verificationUri"])
        self.expires_in = int(message.get("expiresInSeconds") or 900)

    async def wait(self) -> dict[str, Any]:
        """The token payload once the user approved the code; raises if the sign-in failed or was cancelled."""
        message = await self._read()
        await self.cancel()
        credential = message.get("credential")
        if not isinstance(credential, dict):
            raise CodexAuthError("login_failed", "Codex sign-in could not be completed.", 502)
        return payload_from_credential(credential)

    async def _read(self) -> dict[str, Any]:
        proc = self._proc
        if proc is None or proc.stdout is None:
            raise CodexAuthError("login_failed", "Codex sign-in is not running.", 409)
        while True:
            line = await proc.stdout.readline()
            if not line:
                raise CodexAuthError("login_failed", "Codex sign-in ended without a result.", 502)
            try:
                message = json.loads(line)
            except ValueError:
                continue
            if isinstance(message, dict):
                return message

    async def cancel(self) -> None:
        proc, self._proc = self._proc, None
        if proc is None or proc.returncode is not None:
            return
        proc.terminate()
        try:
            await asyncio.wait_for(proc.wait(), 5)
        except TimeoutError:
            proc.kill()
