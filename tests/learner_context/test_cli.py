from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from deeptutor.learner_context import cli, credentials, repository


def test_single_user_cli_creates_scoped_secret_without_printing_it(tmp_path, monkeypatch):
    from deeptutor.multi_user import paths
    from deeptutor.multi_user.context import reset_current_user, set_current_user
    from deeptutor.multi_user.models import CurrentUser, LOCAL_ADMIN_ID, LOCAL_ADMIN_USERNAME, UserScope

    monkeypatch.setattr(paths, "SYSTEM_ROOT", tmp_path / "data" / "system")
    monkeypatch.setattr(paths, "ADMIN_WORKSPACE_ROOT", tmp_path / "data")
    monkeypatch.setattr(paths, "USERS_ROOT", tmp_path / "data" / "users")
    monkeypatch.setenv("PI_TUTOR_LEARNER_CONTEXT_CREDENTIALS_DB", str(tmp_path / "credentials.db"))
    monkeypatch.setattr("deeptutor.services.auth.AUTH_ENABLED", False)

    result = CliRunner().invoke(cli.app, ["credentials-create", "local-test", "--read-only"])
    assert result.exit_code == 0, result.output
    assert "dtlc_" not in result.output

    context = set_current_user(CurrentUser(id=LOCAL_ADMIN_ID, username=LOCAL_ADMIN_USERNAME, role="admin", scope=UserScope(kind="admin", user_id=LOCAL_ADMIN_ID, root=tmp_path / "data")))
    try:
        rows = credentials.list_for_user(LOCAL_ADMIN_ID)
        assert len(rows) == 1
        assert rows[0]["scopes"] == "read"
        token = credentials.load_token(LOCAL_ADMIN_ID, rows[0]["id"])
        assert token and token.startswith(f"dtlc_{LOCAL_ADMIN_ID}.")
    finally:
        reset_current_user(context)


def test_backup_makes_consistent_copy(tmp_path, monkeypatch):
    source = tmp_path / "chat_mastery.sqlite3"
    with repository.connect(source) as con:
        con.execute("INSERT INTO usage_style(ts, session_id, style, quote) VALUES (1, 's', 'tried_first', 'x')")
    monkeypatch.setattr("deeptutor.services.auth.AUTH_ENABLED", False)
    result = CliRunner().invoke(cli.app, ["backup", "--output-dir", str(tmp_path / "backups")])
    assert result.exit_code == 0, result.output
    target = Path(result.output.strip().splitlines()[-1])
    with repository.connect(target) as con:
        assert con.execute("SELECT count(*) FROM usage_style").fetchone()[0] == 1
