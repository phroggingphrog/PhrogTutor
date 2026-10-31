from __future__ import annotations

import sqlite3

from typer.testing import CliRunner

from deeptutor.learner_context import cli, repository


def test_automatic_collection_uses_authenticated_settings_api(monkeypatch):
    class FakeResponse:
        def raise_for_status(self):
            pass

        def json(self):
            return {"automatic_collection_enabled": True}

    class FakeClient:
        def __init__(self):
            self.closed = False
            self.request = None

        def put(self, url, json):
            self.request = (url, json)
            return FakeResponse()

        def close(self):
            self.closed = True

    fake = FakeClient()
    monkeypatch.setattr(cli, "_api_client", lambda: (fake, "http://test"))
    result = CliRunner().invoke(cli.app, ["set-automatic-collection", "true"])
    assert result.exit_code == 0, result.output
    assert fake.request == (
        "http://test/api/learner-context/v1/automatic-collection",
        {"enabled": True},
    )
    assert fake.closed


def test_backup_makes_consistent_copy(tmp_path, monkeypatch):
    source = tmp_path / "chat_mastery.sqlite3"
    with sqlite3.connect(source) as con:
        con.execute("CREATE TABLE usage_style (id INTEGER PRIMARY KEY, ts REAL, session_id TEXT, style TEXT, quote TEXT)")
        con.execute("INSERT INTO usage_style VALUES (1, 1000, 's', 'tried_first', 'quote')")
    monkeypatch.setattr(repository, "database_path", lambda: source)
    result = CliRunner().invoke(cli.app, ["backup", "--output-dir", str(tmp_path / "backups")])
    assert result.exit_code == 0, result.output
    target = tmp_path / "backups" / result.output.strip().splitlines()[-1].split("/")[-1]
    with sqlite3.connect(target) as con:
        assert con.execute("SELECT quote FROM usage_style WHERE id=1").fetchone()[0] == "quote"
    assert target.stat().st_mode & 0o777 == 0o600
