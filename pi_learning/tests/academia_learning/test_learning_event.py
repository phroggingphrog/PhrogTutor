"""Tests for learning_event ledger, source allowlist, and provenance model."""

from __future__ import annotations

import json
from pathlib import Path
import sqlite3
from typing import Any
import pytest

from learning_tracking.academia_learning.db import (
    ensure_learning_event_schema,
    ensure_review_event_schema,
)
from learning_tracking.academia_learning.learning_event import (
    EpistemicClass,
    LearningEvent,
    SourceSensitivity,
    get_learning_event,
    insert_learning_event,
    list_learning_events,
    load_allowed_sources,
    validate_learning_event_payload,
)


def test_learning_event_creation_and_yaml_contract():
    event = LearningEvent(
        id="hermes-cron-monitor-20260921-001",
        source="hermes_runtime",
        sensitivity=SourceSensitivity.OPERATIONAL.value,
        whole_system="Hermes",
        subsystem="Runtime",
        concept="Change detection",
        concept_tags=("cron", "polling", "idempotency", "token-efficiency"),
        observed_fact="The monitor output remained unchanged and suppressed the LLM run.",
        evidence=({"source": "cron_execution", "reference": "execution-id"},),
        lesson={
            "concept": "Change detection avoids unnecessary model calls.",
            "explanation": "A deterministic monitor can gate an expensive reasoning step.",
        },
        exercise={
            "task": "Design a monitor whose unchanged output is byte-stable.",
        },
        verification={
            "command": "repeat the monitor twice and compare hashes",
        },
        mastery="pending",
    )

    yaml_text = event.to_yaml()
    assert "learning_event:" in yaml_text
    assert "  id: hermes-cron-monitor-20260921-001" in yaml_text
    assert "  source: hermes_runtime" in yaml_text
    assert "  sensitivity: operational" in yaml_text
    assert "    - cron" in yaml_text
    assert '  observed_fact: "The monitor output remained unchanged and suppressed the LLM run."' in yaml_text
    assert "  lesson:" in yaml_text
    assert "  exercise:" in yaml_text
    assert "  verification:" in yaml_text
    assert "  mastery: pending" in yaml_text
    assert "  sanitized_case_summary: false" in yaml_text


def test_epistemic_integrity_rejects_speculative_hedges():
    with pytest.raises(ValueError, match="speculative wording"):
        LearningEvent(
            id="speculative-event-001",
            source="hermes_runtime",
            sensitivity="operational",
            whole_system="Hermes",
            subsystem="Runtime",
            concept="Failure diagnosis",
            concept_tags=("test",),
            observed_fact="The server probably crashed because maybe memory was low.",
            evidence=({"source": "log", "reference": "ref-1"},),
            lesson={"concept": "c", "explanation": "e"},
            exercise={"task": "t"},
            verification={"command": "v"},
        )


def test_restricted_source_blocked_without_sanitization():
    with pytest.raises(ValueError, match="restricted source and cannot be ingested without explicit sanitized_case_summary"):
        LearningEvent(
            id="restricted-event-001",
            source="email_content",
            sensitivity="restricted",
            whole_system="Learning",
            subsystem="Restricted evidence",
            concept="Sanitized case",
            concept_tags=("email",),
            observed_fact="Received an email with an attachment.",
            evidence=({"source": "inbox", "reference": "msg-1"},),
            lesson={"concept": "c", "explanation": "e"},
            exercise={"task": "t"},
            verification={"command": "v"},
            sanitized_case_summary=False,
        )

    # Allowed with explicit sanitized_case_summary and restricted sensitivity
    event = LearningEvent(
        id="restricted-event-002",
        source="email_content",
        sensitivity="restricted",
        whole_system="Learning",
        subsystem="Restricted evidence",
        concept="Sanitized case",
        concept_tags=("email",),
        observed_fact="Sanitized notice: submission deadline announced.",
        evidence=({"source": "inbox", "reference": "msg-1"},),
        lesson={"concept": "c", "explanation": "e"},
        exercise={"task": "t"},
        verification={"command": "v"},
        sanitized_case_summary=True,
    )
    assert event.sanitized_case_summary is True
    assert "  sanitized_case_summary: true" in event.to_yaml()

def test_unknown_source_rejected_by_policy_validator(tmp_path: Path):
    policy_file = tmp_path / "strict_policy.json"
    policy_file.write_text("{}", encoding="utf-8")
    payload = {
        "id": "unknown-source-001",
        "source": "unapproved_arbitrary_source",
        "sensitivity": "operational",
        "whole_system": "Learning",
        "subsystem": "Policy",
        "concept": "Source authorization",
        "concept_tags": ["test"],
        "observed_fact": "Some factual observation.",
        "evidence": [{"source": "log", "reference": "ref-1"}],
        "lesson": {"concept": "c", "explanation": "e"},
        "exercise": {"task": "t"},
        "verification": {"command": "v"},
    }
    with pytest.raises(ValueError, match="Unknown learning source 'unapproved_arbitrary_source'"):
        validate_learning_event_payload(payload, policy_path=policy_file)


def test_sensitivity_and_source_mismatch_rejected():
    # Restricted source trying to claim operational sensitivity
    with pytest.raises(ValueError, match="cannot be tagged with sensitivity 'operational'"):
        LearningEvent(
            id="mismatch-event-001",
            source="apple_id",
            sensitivity="operational",
            whole_system="Identity",
            subsystem="Apple ID",
            concept="App passwords",
            concept_tags=("apple",),
            observed_fact="Apple ID app-specific password created.",
            evidence=({"source": "apple", "reference": "ref-1"},),
            lesson={"concept": "c", "explanation": "e"},
            exercise={"task": "t"},
            verification={"command": "v"},
            sanitized_case_summary=True,
        )
    # Safe source trying to claim restricted sensitivity
    with pytest.raises(ValueError, match="cannot be tagged with sensitivity 'restricted'"):
        LearningEvent(
            id="mismatch-event-002",
            source="hermes_runtime",
            sensitivity="restricted",
            whole_system="Identity",
            subsystem="Hermes",
            concept="Restricted source",
            concept_tags=("hermes",),
            observed_fact="Hermes runtime restarted.",
            evidence=({"source": "systemd", "reference": "ref-1"},),
            lesson={"concept": "c", "explanation": "e"},
            exercise={"task": "t"},
            verification={"command": "v"},
        )

    # Invalid sensitivity value
    with pytest.raises(ValueError, match="sensitivity must be one of"):
        LearningEvent(
            id="mismatch-event-003",
            source="hermes_runtime",
            sensitivity="top_secret",
            whole_system="Identity",
            subsystem="Hermes",
            concept="Sensitivity",
            concept_tags=("hermes",),
            observed_fact="Hermes runtime restarted.",
            evidence=({"source": "systemd", "reference": "ref-1"},),
            lesson={"concept": "c", "explanation": "e"},
            exercise={"task": "t"},
            verification={"command": "v"},
        )


def test_load_allowed_sources_fails_closed(tmp_path: Path):
    missing_file = tmp_path / "does_not_exist.json"
    with pytest.raises(FileNotFoundError, match="Learning policy file not found"):
        load_allowed_sources(missing_file, fail_closed=True)

    malformed_file = tmp_path / "malformed.json"
    malformed_file.write_text("{ not valid json", encoding="utf-8")
    with pytest.raises(ValueError, match="Malformed learning policy"):
        load_allowed_sources(malformed_file, fail_closed=True)


def test_load_allowed_sources_from_policy(tmp_path: Path):
    policy_file = tmp_path / "test_policy.json"
    policy_file.write_text(json.dumps({
        "course_to_hermes_map": {
            "database_concepts": {
                "hermes_layers": ["capture", "staging", "provenance"],
            }
        }
    }), encoding="utf-8")

    sources = load_allowed_sources(policy_file)
    assert "hermes_capture" in sources
    assert "hermes_staging" in sources
    assert "hermes_provenance" in sources
    assert "hermes_runtime" in sources


def test_sqlite_persistence_and_additive_schema(db_path: Path):
    event = LearningEvent(
        id="vps-systemd-001",
        source="hermes_runtime",
        sensitivity="operational",
        whole_system="Hermes",
        subsystem="Systemd",
        concept="Signal exits",
        concept_tags=("systemd", "linux"),
        observed_fact="Service hermes-gateway failed with exit code 143.",
        evidence=({"source": "journalctl", "reference": "entry-99"},),
        lesson={
            "concept": "SIGTERM exit code 143",
            "explanation": "Process exited cleanly upon receiving SIGTERM (128 + 15).",
        },
        exercise={
            "task": "Differentiate SIGKILL (137) from SIGTERM (143) in service exits.",
        },
        verification={
            "command": "kill -15 <pid> and check exit status $?",
        },
        mastery="pending",
    )

    conn = sqlite3.connect(db_path)
    try:
        insert_learning_event(conn, event)
        conn.commit()

        retrieved = get_learning_event(conn, "vps-systemd-001")
        assert retrieved is not None
        assert retrieved.id == "vps-systemd-001"
        assert retrieved.concept_tags == ("systemd", "linux")
        assert retrieved.lesson["concept"] == "SIGTERM exit code 143"
        assert retrieved.mastery == "pending"

        events = list_learning_events(conn, source="hermes_runtime")
        assert len(events) == 1
        assert events[0].id == "vps-systemd-001"

        events_empty = list_learning_events(conn, source="nonexistent")
        assert len(events_empty) == 0
    finally:
        conn.close()


def test_schema_migration_is_idempotent(db_path: Path):
    # Calling ensure_learning_event_schema multiple times must succeed without error
    ensure_learning_event_schema(db_path)
    ensure_learning_event_schema(db_path)
    conn = sqlite3.connect(db_path)
    try:
        columns = {
            row[1]: (row[3], row[4])
            for row in conn.execute("PRAGMA table_info(learning_event)")
        }
        assert columns["whole_system"] == (1, None)
        assert columns["subsystem"] == (1, None)
        assert columns["concept"] == (1, None)
    finally:
        conn.close()

def test_legacy_populated_schema_migrates_and_hydrates_explicitly(db_path: Path):
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("DROP TABLE learning_event")
        conn.execute(
            """
            CREATE TABLE learning_event(
                id TEXT PRIMARY KEY,
                source TEXT NOT NULL,
                sensitivity TEXT NOT NULL,
                incident_or_change_ref TEXT,
                concept_tags_json TEXT NOT NULL,
                observed_fact TEXT NOT NULL,
                hypothesis TEXT,
                model_explanation TEXT,
                evidence_json TEXT NOT NULL,
                lesson_json TEXT NOT NULL,
                exercise_json TEXT NOT NULL,
                verified_result TEXT,
                verification_json TEXT NOT NULL,
                mastery TEXT NOT NULL,
                sanitized_case_summary INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            INSERT INTO learning_event(
                id, source, sensitivity, concept_tags_json, observed_fact,
                evidence_json, lesson_json, exercise_json, verification_json,
                mastery, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "legacy-001",
                "hermes_runtime",
                "operational",
                json.dumps(["legacy"]),
                "A legacy row was retained.",
                json.dumps([{"source": "runtime", "reference": "old-1"}]),
                json.dumps({"concept": "legacy concept", "explanation": "legacy explanation"}),
                json.dumps({"task": "legacy task"}),
                json.dumps({"command": "legacy command"}),
                "pending",
                "2026-01-01T00:00:00+00:00",
            ),
        )
        conn.commit()
    finally:
        conn.close()

    ensure_learning_event_schema(db_path)
    conn = sqlite3.connect(db_path)
    try:
        event = get_learning_event(conn, "legacy-001")
        assert event is not None
        assert (event.whole_system, event.subsystem, event.concept) == (
            "legacy",
            "legacy",
            "legacy",
        )
        cols = {
            row[1]: (row[2], row[3], row[4])
            for row in conn.execute("PRAGMA table_info(learning_event)")
        }
        assert cols["whole_system"] == ("TEXT", 0, None)
    finally:
        conn.close()

def test_filesystem_evidence_requires_valid_safe_path(tmp_path: Path):
    # 1. Rejects empty reference
    with pytest.raises(ValueError, match="path must not be empty"):
        LearningEvent(
            id="fs-event-001",
            whole_system="Hermes",
            subsystem="Filesystem",
            concept="Evidence paths",
            source="hermes_runtime",
            sensitivity="operational",
            concept_tags=("fs",),
            observed_fact="Observed file change.",
            evidence=({"source": "filesystem", "reference": ""},),
            lesson={"concept": "c", "explanation": "e"},
            exercise={"task": "t"},
            verification={"command": "v"},
        )

    # 2. validate_learning_event rejects forbidden reference
    from learning_tracking.academia_learning.api import validate_learning_event
    with pytest.raises(PermissionError, match="forbidden root"):
        validate_learning_event(
            {
                "id": "fs-event-002",
                "source": "hermes_runtime",
                "sensitivity": "operational",
                "whole_system": "Hermes",
                "subsystem": "Filesystem",
                "concept": "Evidence paths",
                "concept_tags": ["fs"],
                "observed_fact": "Observed file change.",
                "evidence": [{"source": "filesystem", "reference": "/etc/shadow"}],
                "lesson": {"concept": "c", "explanation": "e"},
                "exercise": {"task": "t"},
                "verification": {"command": "v"},
            }
        )

    # 3. validate_learning_event accepts valid safe path with explicit safe_roots
    safe_file = tmp_path / "safe.log"
    safe_file.write_text("ok", encoding="utf-8")
    validated = validate_learning_event(
        {
            "id": "fs-event-003",
            "source": "hermes_runtime",
            "sensitivity": "operational",
            "whole_system": "Hermes",
            "subsystem": "Filesystem",
            "concept": "Evidence paths",
            "concept_tags": ["fs"],
            "observed_fact": "Observed file change.",
            "evidence": [{"source": "filesystem", "reference": str(safe_file)}],
            "lesson": {"concept": "c", "explanation": "e"},
            "exercise": {"task": "t"},
            "verification": {"command": "v"},
        },
        safe_roots=[tmp_path],
    )
    assert validated["learning_event"]["id"] == "fs-event-003"


def test_validate_learning_event_fails_closed_on_unauthorized_source(tmp_path: Path):
    from learning_tracking.academia_learning.api import validate_learning_event
    policy_file = tmp_path / "strict_policy.json"
    policy_file.write_text(json.dumps({
    }), encoding="utf-8")

    # A raw dictionary with unauthorized source
    bad_payload = {
        "id": "unauth-001",
        "source": "unauthorized_foreign_crawler",
        "sensitivity": "operational",
        "whole_system": "Learning",
        "subsystem": "Policy",
        "concept": "Source authorization",
        "concept_tags": ["crawl"],
        "observed_fact": "Crawled some external data.",
        "evidence": [{"source": "crawler", "reference": "run-1"}],
        "lesson": {"concept": "c", "explanation": "e"},
        "exercise": {"task": "t"},
        "verification": {"command": "v"},
    }
    with pytest.raises(ValueError, match="Unknown learning source"):
        validate_learning_event(bad_payload, policy_path=policy_file)


def test_custom_policy_source_is_validated_outside_constructor(tmp_path: Path):
    policy_file = tmp_path / "custom_policy.json"
    policy_file.write_text(json.dumps({"allowed_sources": ["custom_review_tool"]}), encoding="utf-8")
    payload = {
        "whole_system": "Learning",
        "subsystem": "Policy",
        "concept": "Custom source",
        "id": "custom-source-001",
        "source": "custom_review_tool",
        "sensitivity": "academic",
        "concept_tags": ["custom"],
        "observed_fact": "The custom review tool emitted a stable report.",
        "evidence": [{"source": "custom_review_tool", "reference": "run-1"}],
        "lesson": {"concept": "custom reports", "explanation": "Stable reports are reproducible evidence."},
        "exercise": {"task": "Repeat the report and compare output."},
        "verification": {"command": "compare the two reports"},
    }
    # Construction is structural and must not consult the default policy.
    event = LearningEvent.from_dict(payload)
    assert event.source == "custom_review_tool"
    validated = validate_learning_event_payload(payload, policy_path=policy_file)
    assert validated.source == "custom_review_tool"


def _raw_event_payload() -> dict[str, Any]:
    return {
        "id": "strict-raw-001",
        "source": "hermes_runtime",
        "sensitivity": "operational",
        "whole_system": "Learning",
        "subsystem": "Runtime",
        "concept": "Strict validation",
        "concept_tags": ["validation"],
        "observed_fact": "The validator rejected malformed input.",
        "evidence": [{"source": "runtime", "reference": "run-1"}],
        "lesson": {"concept": "strict types", "explanation": "Raw values are not coerced."},
        "exercise": {"task": "Submit a typed payload."},
        "verification": {"command": "validate the payload"},
    }


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("whole_system", None),
        ("subsystem", None),
        ("concept", None),
        ("observed_fact", True),
        ("id", 42),
        ("source", False),
        ("sensitivity", None),
        ("sensitivity", 1),
        ("incident_or_change_ref", 3),
        ("hypothesis", False),
        ("created_at", []),
        ("sanitized_case_summary", 1),
    ],
)
def test_raw_payload_rejects_missing_or_non_string_values(field: str, value: Any):
    payload = _raw_event_payload()
    if value is None and field in {"whole_system", "subsystem", "concept"}:
        payload.pop(field)
    else:
        payload[field] = value
    with pytest.raises((TypeError, ValueError)):
        LearningEvent.from_dict(payload)


def test_raw_payload_rejects_non_string_nested_values_and_scalar_tags():
    payload = _raw_event_payload()
    payload["concept_tags"] = "validation"
    with pytest.raises(ValueError, match="concept_tags"):
        LearningEvent.from_dict(payload)

    payload = _raw_event_payload()
    payload["evidence"][0]["reference"] = 7
    with pytest.raises(ValueError, match="evidence reference"):
        LearningEvent.from_dict(payload)

    payload = _raw_event_payload()
    payload["lesson"]["explanation"] = 1
    with pytest.raises(ValueError, match="lesson"):
        LearningEvent.from_dict(payload)

def test_restricted_source_round_trip_persists_sanitized_case_summary(db_path: Path):
    event = LearningEvent(
        id="restricted-round-trip-001",
        source="email_content",
        sensitivity="restricted",
        concept_tags=("privacy",),
        observed_fact="Sanitized case summary records a deadline notice.",
        evidence=({"source": "email_content", "reference": "sanitized-case-1"},),
        lesson={"concept": "redaction", "explanation": "Store only a sanitized summary."},
        exercise={"task": "Identify which details must be redacted."},
        verification={"command": "review the redaction checklist"},
        whole_system="Learning",
        subsystem="Provenance",
        concept="Restricted evidence",
        incident_or_change_ref="case-1",
        hypothesis="Redaction preserves useful structure.",
        model_explanation="The summary removes personal content while retaining the event.",
        verified_result="Reviewer confirmed no raw personal content remains.",
        sanitized_case_summary=True,
    )
    conn = sqlite3.connect(db_path)
    try:
        insert_learning_event(conn, event)
        conn.commit()
        retrieved = get_learning_event(conn, event.id)
        listed = list_learning_events(conn, source="email_content")
    finally:
        conn.close()
    assert retrieved is not None
    assert retrieved.sanitized_case_summary is True
    assert retrieved.whole_system == "Learning"
    assert retrieved.model_explanation == event.model_explanation
    assert listed[0].sanitized_case_summary is True


def test_learning_event_api_type_hints_resolve():
    import typing
    from learning_tracking.academia_learning import api

    record_hints = typing.get_type_hints(api.record_learning_event)
    validate_hints = typing.get_type_hints(api.validate_learning_event)
    assert record_hints["event"] is Any
    assert validate_hints["event"] is Any
    assert "safe_roots" in record_hints
    assert "safe_roots" in validate_hints


def test_record_learning_event_dry_run_default(db_path: Path):
    from learning_tracking.academia_learning.api import record_learning_event
    event = LearningEvent(
        id="dryrun-test-001",
        source="hermes_runtime",
        sensitivity="operational",
        whole_system="Learning",
        subsystem="Dry run",
        concept="Validation",
        concept_tags=("dryrun",),
        observed_fact="A dry run test event.",
        evidence=({"source": "runtime", "reference": "ref-1"},),
        lesson={"concept": "dry run", "explanation": "verifies without mutation"},
        exercise={"task": "t"},
        verification={"command": "v"},
    )

    # 1. Default is dry-run
    result = record_learning_event(event, db_path=db_path)
    assert result["dry_run"] is True
    assert result["validated"] is True

    # Verify row was NOT inserted into SQLite
    conn = sqlite3.connect(db_path)
    try:
        assert get_learning_event(conn, "dryrun-test-001") is None
    finally:
        conn.close()

    # 2. Explicit dry_run=False commits the write
    commit_res = record_learning_event(event, dry_run=False, db_path=db_path)
    assert commit_res["dry_run"] is False
    assert commit_res["committed"] is True

    conn = sqlite3.connect(db_path)
    try:
        saved = get_learning_event(conn, "dryrun-test-001")
        assert saved is not None
        assert saved.id == "dryrun-test-001"
    finally:
        conn.close()
