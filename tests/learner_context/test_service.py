from __future__ import annotations

import sqlite3
import time

import pytest

from deeptutor.learner_context import repository, service


def test_additive_migration_preserves_old_rows_and_defaults_pi_tutor(tmp_path):
    path = tmp_path / "legacy.sqlite3"
    with sqlite3.connect(path) as con:
        con.execute("CREATE TABLE usage_style (id INTEGER PRIMARY KEY, ts REAL, session_id TEXT, style TEXT, quote TEXT)")
        con.execute("INSERT INTO usage_style VALUES (1, 1000, 'old-session', 'tried_first', 'my attempt')")
    profile = service.get_profile(now=2000, path=path)
    assert profile["signals"] == 1
    assert profile["records"][0]["source"] == "pi-tutor"
    assert profile["records"][0]["event_id"] == ""
    assert profile["records"][0]["quote"] == "my attempt"
    assert service.get_prompt_context(path=path) == ""
    with repository.connect(path) as con:
        assert con.execute("SELECT quote FROM usage_style WHERE id=1").fetchone()[0] == "my attempt"


def test_profile_requires_8_signals_and_40_percent_share(tmp_path):
    path = tmp_path / "profile.sqlite3"
    for i in range(7):
        assert service.record_interaction_style(style="tried_first", event_id=f"e{i}", source="pi", path=path)
    assert service.get_profile(now=time.time(), path=path)["dominant"] is None
    service.record_interaction_style(style="tried_first", event_id="e7", source="pi", path=path)
    assert service.get_profile(now=time.time(), path=path)["dominant"] == "tried_first"
    service.record_interaction_style(style="accepted", event_id="e8", source="pi", path=path)
    profile = service.get_profile(now=time.time(), path=path)
    assert profile["dominant"] == "tried_first"
    assert profile["shares"]["tried_first"] >= 0.4


def test_recency_weights_recent_signals_more(tmp_path):
    path = tmp_path / "decay.sqlite3"
    now = 2_000_000_000.0
    for i in range(3):
        service.record_interaction_style(style="accepted", event_id=f"old-{i}", source="pi", ts=now - 30 * 86400, path=path)
    for i in range(5):
        service.record_interaction_style(style="tried_first", event_id=f"recent-{i}", source="dsh", ts=now, path=path)
    profile = service.get_profile(now=now, path=path)
    assert profile["shares"]["tried_first"] > profile["shares"]["accepted"]
    assert profile["dominant"] == "tried_first"


def test_event_idempotency_is_scoped_by_source(tmp_path):
    path = tmp_path / "dedupe.sqlite3"
    assert service.record_interaction_style(style="tried_first", event_id="same", source="pi", path=path)
    assert not service.record_interaction_style(style="accepted", event_id="same", source="pi", path=path)
    assert service.record_interaction_style(style="accepted", event_id="same", source="dsh", path=path)
    assert service.get_profile(path=path)["signals"] == 2


def test_existing_accounts_do_not_opt_in_to_automatic_collection(tmp_path):
    path = tmp_path / "existing-account.sqlite3"
    profile = service.get_profile(path=path)
    assert profile["enabled"] is True
    assert profile["automatic_collection_enabled"] is False
    assert service.get_prompt_context(path=path) == ""


def test_opt_out_stops_storage_and_prompt_context(tmp_path, monkeypatch):
    path = tmp_path / "off.sqlite3"
    service.set_enabled(False, path=path)
    assert not service.record_interaction_style(style="tried_first", event_id="e", source="pi", path=path)
    service.set_enabled(True, path=path)
    for i in range(8):
        service.record_interaction_style(style="tried_first", event_id=str(i), source="pi", path=path)
    from deeptutor.services import chat_mastery
    from deeptutor.multi_user.context import set_current_user, reset_current_user
    from deeptutor.multi_user.models import CurrentUser, UserScope

    token = set_current_user(CurrentUser(id="u_optin", username="owner", role="user", scope=UserScope(kind="user", user_id="u_optin", root=tmp_path)))
    monkeypatch.setattr(repository, "database_path", lambda: path)
    try:
        assert chat_mastery.set_style_collection_enabled(True)
        assert service.get_prompt_context(automatic_collection_enabled=True)
        assert chat_mastery._style_collection_enabled()
        assert chat_mastery.set_style_collection_enabled(False) is False
        assert not chat_mastery._style_collection_enabled()
        assert chat_mastery.record_turn("s", "t", "synthetic learner text", "synthetic assistant text") is None
        assert service.get_prompt_context(automatic_collection_enabled=True)
        assert not chat_mastery._TASKS
    finally:
        reset_current_user(token)
    service.set_enabled(False, path=path)
    assert not service.get_prompt_context(path=path)
    assert service.get_profile(path=path)["signals"] == 8


def test_prompt_export_contains_only_instruction_not_quotes(tmp_path):
    path = tmp_path / "prompt.sqlite3"
    for i in range(8):
        service.record_interaction_style(style="own_idea", event_id=str(i), source="pi", quote="private excerpt", path=path)
    prompt = service.get_prompt_context(path=path, automatic_collection_enabled=True)
    assert "Build on their idea" in prompt
    assert "advisory only" in prompt
    assert "Do not replace or change an active user-selected mode" in prompt
    assert "private excerpt" not in prompt
    assert "private excerpt" not in service.export_profile(format="text", path=path)
    assert '"quote": "private excerpt"' in service.export_profile(format="json", path=path)
    assert '"quote": ""' in service.export_profile(format="json", path=path, include_quotes=False)


def test_invalid_styles_and_payload_sizes_are_rejected(tmp_path):
    bad_path = tmp_path / "bad.sqlite3"
    with pytest.raises(ValueError):
        service.record_interaction_style(style="adhd", event_id="e", path=bad_path)
    with pytest.raises(ValueError):
        service.record_interaction_style(style="accepted", event_id="", path=bad_path)
    with pytest.raises(ValueError):
        service.record_interaction_style(style="accepted", event_id="bad id", path=bad_path)
    with pytest.raises(ValueError):
        service.record_interaction_style(style="accepted", event_id="e", source="bad source", path=bad_path)
    with pytest.raises(ValueError):
        service.record_interaction_style(style="accepted", event_id="e", ts=float("nan"), path=bad_path)


def test_disabled_profile_has_no_instruction(tmp_path):
    path = tmp_path / "disable.sqlite3"
    for i in range(8):
        service.record_interaction_style(style="pushed_back", event_id=str(i), source="pi", path=path)
    service.set_enabled(False, path=path)
    profile = service.get_profile(path=path)
    assert profile["dominant"] is None
    assert profile["instruction"] == ""


def test_classifier_parses_only_allowed_labels():
    from deeptutor.learner_context.classifier import _parse_style

    assert _parse_style('{"style":"pushed_back"}') == "pushed_back"
    assert _parse_style('{"style":"adhd"}') is None
    assert _parse_style("not JSON") is None


def test_external_harness_profile_omits_quotes_and_records(tmp_path):
    path = tmp_path / "integration.sqlite3"
    for index in range(8):
        service.record_interaction_style(style="own_idea", event_id=str(index), source="dsh", quote="private quote", path=path)
    profile = service.get_profile(path=path, include_records=False, include_quotes=False)
    assert profile["dominant"] == "own_idea"
    assert profile["records"] == []
    assert "private quote" not in str(profile)


def test_workspace_database_style_rows_are_copied_once_to_account_store(tmp_path, monkeypatch):
    workspace_path = tmp_path / "workspace.sqlite3"
    account_path = tmp_path / "account.sqlite3"
    monkeypatch.setattr(repository, "database_path", lambda: account_path)
    with repository.connect(workspace_path) as source:
        source.execute(
            "INSERT INTO usage_style(ts, session_id, style, quote, source, event_id) "
            "VALUES (1000, 'session', 'tried_first', 'attempt', 'dsh', 'event-1')"
        )
        assert repository.migrate_legacy_workspace_style_rows(source) == 1
        assert repository.migrate_legacy_workspace_style_rows(source) == 0
    with repository.connect(account_path) as account:
        row = account.execute("SELECT style, quote, source, event_id FROM usage_style").fetchone()
        assert tuple(row) == ("tried_first", "attempt", "dsh", "event-1")
    with repository.connect(workspace_path) as source:
        assert source.execute("SELECT COUNT(*) FROM usage_style").fetchone()[0] == 1


def test_pi_tutor_turn_imports_workspace_style_without_deleting_source(tmp_path, monkeypatch):
    account_path = tmp_path / "account.sqlite3"
    workspace_root = tmp_path / "workspace"
    workspace_path = workspace_root / ".deeptutor" / "data" / "user" / "chat_mastery.sqlite3"
    monkeypatch.setattr(repository, "database_path", lambda: account_path)

    from deeptutor.services.workspace.context import WorkspaceScope, workspace_context

    with repository.connect(workspace_path) as workspace:
        workspace.execute(
            "INSERT INTO usage_style(ts, session_id, style, quote) VALUES (1000, 's', 'tried_first', 'legacy attempt')"
        )
    scope = WorkspaceScope("ws-test", tmp_path, workspace_root)
    with workspace_context(scope):
        assert service.record_interaction_style(
            style="accepted",
            event_id="new-turn",
            source="pi-tutor",
            session_id="s2",
            allow_workspace_migration=True,
        )
    profile = service.get_profile(path=account_path)
    assert profile["signals"] == 2
    assert {record["quote"] for record in profile["records"]} == {"legacy attempt", ""}
    with repository.connect(workspace_path) as workspace:
        assert workspace.execute("SELECT COUNT(*) FROM usage_style").fetchone()[0] == 1
