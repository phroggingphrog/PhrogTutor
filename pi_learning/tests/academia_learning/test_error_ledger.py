"""Tests for error ledger, misconception tracking, and mastery/FSRS integration."""

from __future__ import annotations

from pathlib import Path
import sqlite3
import pytest

from learning_tracking.academia_learning import api
from learning_tracking.academia_learning.db import connect
from learning_tracking.academia_learning.error_ledger import (
    ErrorLedgerError,
    record_error,
    list_errors,
    get_error_summary,
)
from learning_tracking.academia_learning.mastery import compute_mastery


def test_record_error_persists_with_provenance_and_audit(db_path: Path):
    res = api.record_error(
        objective_version_id="db:relational-basics:v1",
        error_category="deviation",
        misconception="Believed foreign keys automatically enforce cascade without PRAGMA foreign_keys=ON",
        failed_step="Verification step 2: child record cascade deletion",
        verification_command="sqlite3 test.db 'DELETE FROM parent WHERE id=1'",
        observed_output="Constraint failed / no rows affected",
        expected_output="Child records deleted",
        correction="Must issue PRAGMA foreign_keys = ON on every new sqlite3 connection",
        actor="student:phrog",
        details={"environment": "sqlite-3.45"},
        db_path=db_path,
    )

    assert res["id"].startswith("err:")
    assert res["objective_version_id"] == "db:relational-basics:v1"
    assert res["error_category"] == "deviation"
    assert "Believed foreign keys" in res["misconception"]
    assert res["actor"] == "student:phrog"
    assert res["details"]["environment"] == "sqlite-3.45"

    # Verify audit event was logged
    db = connect(db_path, write=False)
    try:
        audit_row = db.execute(
            "SELECT * FROM audit_event WHERE target_ref = ?", (res["id"],)
        ).fetchone()
        assert audit_row is not None
        assert audit_row["event_type"] == "ACADEMIA_ERROR_RECORDED"
        assert audit_row["actor"] == "student:phrog"
    finally:
        db.close()


def test_list_and_summarize_errors(db_path: Path):
    api.record_error(
        objective_version_id="db:normalisation:v1",
        error_category="structural",
        misconception="Missing knowledge of 3NF transitive dependencies",
        failed_step="Decomposition step",
        db_path=db_path,
    )
    api.record_error(
        objective_version_id="db:normalisation:v1",
        error_category="application",
        misconception="Accidentally dropped candidate key attribute during split",
        failed_step="Lossless join check",
        correction="Preserve candidate keys in at least one projection",
        db_path=db_path,
    )
    api.record_error(
        objective_version_id="db:relational-basics:v1",
        error_category="syntax",
        misconception="Typo in CREATE TABLE check constraint",
        db_path=db_path,
    )

    all_norm = api.list_errors(objective_version_id="db:normalisation:v1", db_path=db_path)
    assert len(all_norm) == 2

    structural_only = api.list_errors(
        objective_version_id="db:normalisation:v1",
        error_category="structural",
        db_path=db_path,
    )
    assert len(structural_only) == 1
    assert structural_only[0]["error_category"] == "structural"

    summary = api.error_summary(objective_version_id="db:normalisation:v1", db_path=db_path)
    assert summary["total_errors"] == 2
    assert summary["by_category"]["structural"] == 1
    assert summary["by_category"]["application"] == 1
    assert len(summary["misconceptions"]) == 2
    assert "Decomposition step" in summary["failed_steps"]


def test_record_error_validation_rejects_invalid_inputs(db_path: Path):
    with pytest.raises(ErrorLedgerError, match="Invalid error_category"):
        api.record_error(
            objective_version_id="db:normalisation:v1",
            error_category="nonexistent_bad_category",
            misconception="Invalid",
            db_path=db_path,
        )

    with pytest.raises(ErrorLedgerError, match="misconception must be a non-empty string"):
        api.record_error(
            objective_version_id="db:normalisation:v1",
            error_category="deviation",
            misconception="",
            db_path=db_path,
        )


def test_mastery_reflects_error_ledger(db_path: Path):
    # Check baseline status
    status_before = api.mastery_status("db:relational-basics:v1", db_path=db_path)
    assert status_before["error_count"] == 0
    assert status_before["unresolved_misconceptions"] == []

    # Record an error with an uncorrected misconception
    api.record_error(
        objective_version_id="db:relational-basics:v1",
        error_category="deviation",
        misconception="Assumed primary keys can contain NULL in SQLite",
        db_path=db_path,
    )

    status_after = api.mastery_status("db:relational-basics:v1", db_path=db_path)
    assert status_after["error_count"] == 1
    assert "Assumed primary keys can contain NULL in SQLite" in status_after["unresolved_misconceptions"]
    assert "1 error(s) in ledger (1 unresolved)" in status_after["reason"]


def test_schedule_error_remediation_triggers_fsrs_review(db_path: Path):
    err = api.record_error(
        objective_version_id="db:relational-basics:v1",
        error_category="deviation",
        misconception="Forgot natural join eliminates duplicate column names",
        db_path=db_path,
    )

    receipt = api.schedule_error_remediation(
        objective_version_id="db:relational-basics:v1",
        error_id=err["id"],
        misconception=err["misconception"],
        rating="AGAIN",
        db_path=db_path,
    )

    assert receipt["rating"] == "AGAIN"
    assert receipt["verified_result"] == "INCORRECT"
    assert receipt["next_interval_days"] is not None
    assert receipt["next_review_at"] is not None
