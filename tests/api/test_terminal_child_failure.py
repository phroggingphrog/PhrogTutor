from __future__ import annotations

import os

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from deeptutor.api.routers import terminal


@pytest.mark.skipif(not hasattr(os, "fork"), reason="needs fork")
def test_failed_sandbox_start_exits_the_child_and_keeps_the_server_alive(monkeypatch, tmp_path):
    """If the sandbox cannot start, the forked child must exit, not run the server's code."""

    def boom(_home):
        raise RuntimeError("no sandbox here")

    async def allow(_websocket):
        return object()

    monkeypatch.setattr(terminal, "_argv", boom)
    monkeypatch.setattr(terminal, "HOME", tmp_path)
    from deeptutor.api.routers import auth

    monkeypatch.setattr(auth, "ws_require_auth", allow)
    monkeypatch.setattr(auth, "ws_auth_failed", object())

    app = FastAPI()
    app.include_router(terminal.router)
    with TestClient(app) as client:
        with client.websocket_connect("/terminal?note=t") as ws:
            seen = b""
            while b"Terminal unavailable" not in seen:
                seen += ws.receive_bytes()
        assert b"no sandbox here" in seen
        # The server process is still serving after the child exited.
        with client.websocket_connect("/terminal?note=t2") as ws2:
            assert b"Terminal unavailable" in ws2.receive_bytes()
