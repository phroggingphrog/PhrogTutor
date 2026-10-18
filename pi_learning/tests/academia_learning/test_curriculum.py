"""Tests for top-down project learning loop and curriculum generator."""

from __future__ import annotations

import sqlite3

from learning_tracking.academia_learning.learning_event import (
    get_learning_event,
    insert_learning_event,
    LearningEvent,
)
from pathlib import Path
import pytest

from learning_tracking.academia_learning.curriculum import generate_learning_event_from_incident


def test_generate_learning_event_from_incident_success(tmp_path: Path):
    safe_root = tmp_path / "runtime"
    safe_root.mkdir()
    evidence_file = safe_root / "cron_monitor.log"
    evidence_file.write_text("output stable", encoding="utf-8")

    event = generate_learning_event_from_incident(
        event_id="hermes-cron-monitor-20260921-001",
        system="Hermes automation",
        subsystem="gateway and cron",
        concept="change detection",
        concept_tags=["polling", "idempotency", "token-efficiency"],
        observed_fact="The monitor output remained unchanged and suppressed the LLM run.",
        evidence_source="hermes_runtime",
        evidence_reference="execution-id-981",
        explanation="A deterministic monitor can gate an expensive reasoning step.",
        exercise_task="Design a monitor whose unchanged output is byte-stable.",
        verification_command="repeat the monitor twice and compare hashes",
        evidence_file_path=evidence_file,
        safe_roots=[safe_root],
    )

    assert event.id == "hermes-cron-monitor-20260921-001"
    assert "hermes-automation" in event.concept_tags
    assert "gateway-and-cron" in event.concept_tags
    assert "idempotency" in event.concept_tags
    assert event.lesson["concept"] == "change detection"
    assert "[Hermes automation -> gateway and cron -> change detection]" in event.lesson["explanation"]
    assert event.exercise["task"] == "Design a monitor whose unchanged output is byte-stable."
    assert event.verification["command"] == "repeat the monitor twice and compare hashes"
    assert len(event.evidence) == 2
    assert event.evidence[1]["source"] == "filesystem"
    assert event.evidence[1]["reference"] == str(evidence_file.resolve())

    # Check YAML contract
    yaml_text = event.to_yaml()
    assert "learning_event:" in yaml_text
    assert "  id: hermes-cron-monitor-20260921-001" in yaml_text
    assert "  source: hermes_runtime" in yaml_text
    assert "  mastery: pending" in yaml_text


def test_curriculum_dict_and_sqlite_round_trip(db_path: Path):
    event = generate_learning_event_from_incident(
        event_id="round-trip-001",
        system="Hermes",
        subsystem="Runtime",
        concept="Round trips",
        concept_tags=["typed payloads"],
        observed_fact="A generated event retained its hierarchy.",
        evidence_source="hermes_runtime",
        evidence_reference="run-1",
        explanation="Typed conversion preserves epistemic fields.",
        exercise_task="Convert the event to a dictionary and back.",
        verification_command="compare hierarchy fields",
    )
    restored = LearningEvent.from_dict(event.to_dict())
    assert restored.to_dict() == event.to_dict()
    assert "  whole_system: Hermes" in event.to_yaml()
    assert "  subsystem: Runtime" in event.to_yaml()
    assert "  concept: Round trips" in event.to_yaml()

    conn = sqlite3.connect(db_path)
    try:
        insert_learning_event(conn, restored)
        conn.commit()
        loaded = get_learning_event(conn, restored.id)
    finally:
        conn.close()
    expected = event.to_dict()
    expected["learning_event"]["created_at"] = loaded.created_at
    assert loaded.to_dict() == expected


def test_generate_learning_event_rejects_unsafe_evidence_file(tmp_path: Path):
    safe_root = tmp_path / "safe"
    forbidden_root = tmp_path / "forbidden"
    safe_root.mkdir()
    forbidden_root.mkdir()

    secret_file = forbidden_root / "auth.json"
    secret_file.write_text("{}", encoding="utf-8")

    with pytest.raises(PermissionError, match="forbidden root or sensitive file"):
        generate_learning_event_from_incident(
            event_id="unsafe-event-001",
            system="Auth",
            subsystem="Tokens",
            concept="Leaks",
            concept_tags=["security"],
            observed_fact="Attempted to reference a secret file.",
            evidence_source="hermes_runtime",
            evidence_reference="ref-1",
            explanation="Credentials must never be referenced directly.",
            exercise_task="Do not ingest raw auth tokens.",
            verification_command="check allowlist",
            evidence_file_path=secret_file,
            safe_roots=[safe_root],
            forbidden_roots=[forbidden_root],
        )
