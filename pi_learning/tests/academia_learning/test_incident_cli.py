"""CLI integration tests for explicit incident recording."""

from __future__ import annotations

import json
from pathlib import Path
import sqlite3

from learning_tracking.__main__ import main
from learning_tracking.academia_learning.learning_event import get_learning_event


def _args(db_path: Path, policy_path: Path) -> list[str]:
    return [
        "--db-path",
        str(db_path),
        "record-incident",
        "--event-id",
        "cli-incident-001",
        "--system",
        "Hermes",
        "--subsystem",
        "Runtime",
        "--concept",
        "Change detection",
        "--concept-tag",
        "idempotency",
        "--observed-fact",
        "The monitor suppressed an unchanged run.",
        "--evidence-source",
        "hermes_runtime",
        "--evidence-reference",
        "run-1",
        "--explanation",
        "Stable observations can gate expensive work.",
        "--exercise-task",
        "Repeat the monitor and compare outputs.",
        "--verification-command",
        "compare the repeated outputs",
        "--policy-path",
        str(policy_path),
    ]


def test_record_incident_dry_run_then_explicit_commit(db_path: Path, tmp_path: Path, capsys):
    policy_path = tmp_path / "policy.json"
    policy_path.write_text(json.dumps({"allowed_sources": ["hermes_runtime"]}), encoding="utf-8")

    assert main(_args(db_path, policy_path)) == 0
    dry_run = json.loads(capsys.readouterr().out)
    assert dry_run["receipt"]["dry_run"] is True
    assert dry_run["receipt"]["event"]["learning_event"]["mastery"] == "pending"

    conn = sqlite3.connect(db_path)
    try:
        assert get_learning_event(conn, "cli-incident-001") is None
    finally:
        conn.close()

    assert main(_args(db_path, policy_path) + ["--commit"]) == 0
    committed = json.loads(capsys.readouterr().out)
    assert committed["receipt"] == {
        "dry_run": False,
        "committed": True,
        "event_id": "cli-incident-001",
    }

    conn = sqlite3.connect(db_path)
    try:
        saved = get_learning_event(conn, "cli-incident-001")
        assert saved is not None
        assert (saved.whole_system, saved.subsystem, saved.concept) == (
            "Hermes",
            "Runtime",
            "Change detection",
        )
        assert saved.mastery == "pending"
    finally:
        conn.close()
