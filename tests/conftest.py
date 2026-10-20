"""
Root conftest — shared fixtures for the entire test suite.
"""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from deeptutor.core.capability_protocol import CapabilityManifest, TurnCapability
from deeptutor.core.context import Attachment, UnifiedContext
from deeptutor.runtime.stream_bus import StreamBus

# ---------------------------------------------------------------------------
# Multi-user legacy migration guard
# ---------------------------------------------------------------------------


def _tree_snapshot(root: Path) -> frozenset[str]:
    if not root.is_dir():
        return frozenset()
    return frozenset(str(path) for path in root.rglob("*") if path.is_file())


#: Captured at import time — before any test can monkeypatch the roots — so the
#: guard below always watches the developer's real tree, whatever a test does.
_REAL_OWNER_SECRET_TREES: tuple[Path, ...] = ()
try:  # pragma: no cover - import-time wiring
    from deeptutor.multi_user.paths import ADMIN_WORKSPACE_ROOT as _REAL_ADMIN_ROOT
    from deeptutor.multi_user.paths import SYSTEM_ROOT as _REAL_SYSTEM_ROOT

    _REAL_OWNER_SECRET_TREES = (
        _REAL_SYSTEM_ROOT / "user-secrets",
        _REAL_SYSTEM_ROOT / "user-mcp",
        _REAL_SYSTEM_ROOT / "user-cli-apps",
        # Not per-owner, but the same failure: a test that forgets to redirect
        # the roots would record installs the developer's running instance then
        # offers to a chat turn, for apps that are not on disk.
        _REAL_ADMIN_ROOT / "cli-apps",
    )
except Exception:  # pragma: no cover
    pass


@pytest.fixture(autouse=True)
def _guard_real_owner_secrets():
    """A test must never write into the real per-account state trees.

    These hold OAuth refresh tokens, MCP credentials, and which CLI apps are
    installed. A test that redirects ``ADMIN_WORKSPACE_ROOT`` but forgets
    ``SYSTEM_ROOT`` — or that calls ``monkeypatch.undo()`` and so reverts a
    fixture's redirection — lands here, and without this guard the failure is
    silent: the test passes while the developer's tree quietly grows files named
    after fixture users.
    """
    before = {root: _tree_snapshot(root) for root in _REAL_OWNER_SECRET_TREES}
    yield
    for root, snapshot in before.items():
        added = _tree_snapshot(root) - snapshot
        if added:
            for path in added:
                Path(path).unlink(missing_ok=True)
            pytest.fail(
                "test wrote into the real per-account state tree "
                f"{root}: {sorted(added)}. Redirect paths.SYSTEM_ROOT and "
                "paths.ADMIN_WORKSPACE_ROOT (see "
                "tests/services/codex_auth/test_credential_location.py)."
            )


@pytest.fixture(autouse=True)
def _guard_legacy_multi_user_migration(monkeypatch):
    """Tests must never migrate the developer's real ``multi-user/`` tree.

    ``migrate_legacy_multi_user_tree`` runs on the auth/grants/workspace read
    paths, so any test that exercises those without full path isolation would
    otherwise move a real sibling ``multi-user/`` into ``data/``. Point the
    legacy root at a path that cannot exist and reset the once-flag;
    migration tests opt back in by patching the constants themselves.
    """
    from deeptutor.multi_user import paths

    monkeypatch.setattr(
        paths, "LEGACY_MULTI_USER_ROOT", Path("/nonexistent/deeptutor-legacy-multi-user")
    )
    monkeypatch.setattr(paths, "_legacy_migration_done", False)
    yield


@pytest.fixture(autouse=True)
def _isolate_codebuddy_login(monkeypatch):
    """Hide the developer's real CodeBuddy session from every test.

    The CodeBuddy provider reads the OAuth session the IDE plugin / CLI stores
    outside the repo, so without this a developer who is signed in gets
    different results than CI. Tests that exercise the signed-in path point the
    override at a fixture file.
    """
    from deeptutor.services import codebuddy_credentials

    monkeypatch.setenv(
        "DEEPTUTOR_CODEBUDDY_AUTH_FILE", str(Path("/nonexistent/codebuddy-auth.info"))
    )
    monkeypatch.setattr(
        codebuddy_credentials,
        "_local_storage_dir",
        lambda: Path("/nonexistent/codebuddy-local-storage"),
    )
    monkeypatch.delenv("CODEBUDDY_API_KEY", raising=False)
    monkeypatch.delenv("CODEBUDDY_BASE_URL", raising=False)
    monkeypatch.delenv("CODEBUDDY_INTERNET_ENVIRONMENT", raising=False)
    monkeypatch.delenv("DEEPTUTOR_CODEBUDDY_BACKEND", raising=False)
    yield


# ---------------------------------------------------------------------------
# StreamBus
# ---------------------------------------------------------------------------


@pytest.fixture
def stream_bus() -> StreamBus:
    """Fresh StreamBus for one test."""
    return StreamBus()


# ---------------------------------------------------------------------------
# UnifiedContext
# ---------------------------------------------------------------------------


@pytest.fixture
def minimal_context() -> UnifiedContext:
    """Context with just a user message."""
    return UnifiedContext(
        session_id="test-session",
        user_message="Hello",
        language="en",
    )


@pytest.fixture
def rich_context() -> UnifiedContext:
    """Context with attachments, tools, KB, and metadata."""
    return UnifiedContext(
        session_id="test-session",
        user_message="Explain RAG",
        conversation_history=[
            {"role": "user", "content": "What is AI?"},
            {"role": "assistant", "content": "AI is..."},
        ],
        enabled_tools=["rag", "web_search"],
        active_capability="deep_solve",
        knowledge_bases=["my-kb"],
        attachments=[Attachment(type="image", url="https://img.png")],
        config_overrides={"temperature": 0.7},
        language="en",
        metadata={"turn_id": "t-1"},
    )


# ---------------------------------------------------------------------------
# SQLiteSessionStore (in-memory / tmp)
# ---------------------------------------------------------------------------


@pytest.fixture
def tmp_db_path(tmp_path: Path) -> Path:
    """Temporary database file path."""
    return tmp_path / "test_chat.db"


@pytest.fixture
def sqlite_store(tmp_db_path: Path):
    """SQLiteSessionStore backed by a temp file."""
    from deeptutor.services.session.sqlite_store import SQLiteSessionStore

    return SQLiteSessionStore(db_path=tmp_db_path)


# ---------------------------------------------------------------------------
# Fake / stub capability
# ---------------------------------------------------------------------------


class _StubCapability(TurnCapability):
    """Capability that emits one content event and returns."""

    manifest = CapabilityManifest(
        name="stub",
        description="Stub for testing.",
        stages=["responding"],
    )

    async def run(self, context: UnifiedContext, stream: StreamBus) -> None:
        await stream.content("stub response", source=self.name)


@pytest.fixture
def stub_capability() -> _StubCapability:
    return _StubCapability()


# ---------------------------------------------------------------------------
# Fake LLM helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def fake_llm_config() -> MagicMock:
    """MagicMock mimicking LLMConfig with common defaults."""
    cfg = MagicMock()
    cfg.model = "gpt-4o-mini"
    cfg.max_tokens = 4096
    cfg.temperature = 0.7
    cfg.api_key = "sk-test"
    cfg.api_base = "https://api.openai.com/v1"
    return cfg


@pytest.fixture(autouse=True)
def _isolate_llm_config(request, monkeypatch):
    """Give every test the same resolved LLM config, and no cache to inherit.

    ``get_llm_config`` memoises into a module-level ``_LLM_CONFIG_CACHE``, so
    whichever test resolved first decided what every later test saw. Anything
    constructed at import-or-init time off that config — ``QuestionPipeline``
    is one — therefore passed only when some earlier test had already filled
    the cache: run the suite in a different order and it raised
    ``LLMConfigError`` instead. It also meant a developer with a model
    configured and CI without one were running different tests.

    Resolve to a fixed fake and clear the cache on both sides. Tests that
    exercise resolution itself patch ``resolve_llm_runtime_config`` again,
    which wins over this one.
    """
    from deeptutor.services.config.provider_runtime import ResolvedLLMConfig
    from deeptutor.services.llm import config as llm_config_module

    if "real_llm_resolver" in request.keywords:
        # Resolution is what this test is checking. Still clear the cache on
        # both sides so it neither inherits nor leaves one.
        llm_config_module._LLM_CONFIG_CACHE = None
        yield
        llm_config_module._LLM_CONFIG_CACHE = None
        return

    def _resolve(*_args, **_kwargs) -> ResolvedLLMConfig:
        return ResolvedLLMConfig(
            model="gpt-4o-mini",
            provider_name="openai",
            provider_mode="direct",
            binding="openai",
            api_key="sk-test",
            base_url="https://api.openai.com/v1",
            effective_url="https://api.openai.com/v1",
            context_window=128000,
        )

    monkeypatch.setattr(llm_config_module, "resolve_llm_runtime_config", _resolve)
    monkeypatch.setattr(llm_config_module, "_LLM_CONFIG_CACHE", None)
    yield
    llm_config_module._LLM_CONFIG_CACHE = None


@pytest.fixture(autouse=True)
def _isolate_application_container():
    """No test inherits the process-wide container another test built.

    ``get_application_container`` lazily builds once and keeps the result in a
    module global, so the first test to reach it chose the coordinator backend
    for every test after it. A run where that first test had redis settings in
    scope left every later ``start()`` raising "Turn coordination backend is
    unavailable" — including tests asserting on an entirely different error.
    """
    from deeptutor.app import container as container_module

    container_module._default_container = None
    yield
    container_module._default_container = None


@pytest.fixture(autouse=True)
def _restore_process_env():
    """Undo environment a test left behind outside monkeypatch's reach.

    ``monkeypatch.setenv`` is restored for you; a production code path that
    assigns ``os.environ`` directly is not. ``runtime.launcher`` exports
    ``DEEPTUTOR_HOME`` that way to hand the runtime home to its children, so
    every launcher test left the variable pointing at its own ``tmp_path``.
    The directory is deleted at teardown but the variable is not, and each of
    the eight tests that shell out to ``sys.executable`` then ran against a
    home that no longer exists — creating default settings, and logging that
    it did onto the stdout the parent was trying to parse.
    """
    before = dict(os.environ)
    yield
    if os.environ != before:
        os.environ.clear()
        os.environ.update(before)


@pytest.fixture(autouse=True)
def _isolate_usage_ledger(monkeypatch, tmp_path):
    """Mocked model calls must never enter the developer's real usage totals."""
    from deeptutor.services.llm import usage_ledger

    monkeypatch.setattr(usage_ledger, "ledger_path", lambda: tmp_path / "usage.sqlite3")


def _plain(obj):
    """SimpleNamespace test chunks -> the dict shape pi_engine streams."""
    from types import SimpleNamespace

    if isinstance(obj, SimpleNamespace):
        return {k: _plain(v) for k, v in vars(obj).items()}
    if isinstance(obj, list):
        return [_plain(v) for v in obj]
    return obj


def scripted_pi_loop(client, settlement_rounds: int = 3):
    """A stand-in for ``pi_bridge.loop_turn`` that plays a scripted OpenAI-style client.

    The real round rules run in ``pi_engine/loop.ts`` (needs a model), so this keeps only what
    the Python side is tested for: it asks the host the same questions loop.ts asks
    (round_start, chunks, round, finish_check, dispatch) and feeds it the client's chunks.
    """

    async def loop_turn(request, cfg, host):
        messages, tools = list(request["messages"]), request.get("tools")
        budget, step, mode = cfg["budget"], 0, "explore"
        while True:
            reply = await host.ask("round_start", {"messages": messages, "mode": mode, "max_tokens": request.get("max_tokens")})
            messages = reply.get("messages") or messages
            kwargs = {"model": request.get("model"), "messages": messages, "stream": True}
            if tools and mode != "forced":
                kwargs["tools"] = tools
            async for chunk in await client.chat.completions.create(**kwargs):
                raw = _plain(chunk)
                for choice in raw["choices"]:
                    choice.setdefault("index", 0)
                    choice.setdefault("finish_reason", None)
                    for tc in choice["delta"].get("tool_calls") or []:
                        tc.setdefault("type", "function")
                raw.update({"id": "pi", "object": "chat.completion.chunk", "created": 0, "model": request.get("model") or ""})
                await host.chunk(raw)
            done = await host.ask("round", {})
            messages.append(done["assistant"])
            if done["tool_calls"] and mode != "forced":
                out = await host.ask("dispatch", {"tool_calls": done["tool_calls"], "clean": done["clean"], "step": step})
                messages.extend(out["tool_messages"])
                step += 1
                mode = "explore" if step < budget else "settle" if step < budget + settlement_rounds else "forced"
                continue
            check = await host.ask("finish_check", {"text": done["clean"]})
            return {
                "completed": True, "raw": done["text"], "visible": done["visible"], "messages": messages,
                "tools": tools, "rounds": step + 1, "provider_state": done["provider_state"],
            }

    return loop_turn
