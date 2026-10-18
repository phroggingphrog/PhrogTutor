"""Explicit error ledger, misconception tracking, and remediation integration.

Provides structured persistence for learner misconceptions, failed verification
steps, and error categories with full provenance and audit logging. Integrates
directly with BKT mastery calculation and FSRS spaced retrieval.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import sqlite3
from typing import Any, Dict, List, Optional, Sequence
import uuid

from .db import now_iso
from .grading import ERROR_TAXONOMY as BASE_ERROR_TAXONOMY

EXTENDED_ERROR_TAXONOMY = frozenset({
    *BASE_ERROR_TAXONOMY,
    "syntax",
    "logic",
    "runtime",
    "verification_failed",
})


class ErrorLedgerError(RuntimeError):
    """Raised when an error ledger operation fails validation or persistence."""


@dataclass(frozen=True)
class ErrorLedgerEntry:
    id: str
    objective_version_id: str
    attempt_id: Optional[str]
    error_category: str
    misconception: str
    failed_step: Optional[str]
    verification_command: Optional[str]
    observed_output: Optional[str]
    expected_output: Optional[str]
    correction: Optional[str]
    actor: str
    recorded_at: str
    details: Dict[str, Any]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "objective_version_id": self.objective_version_id,
            "attempt_id": self.attempt_id,
            "error_category": self.error_category,
            "misconception": self.misconception,
            "failed_step": self.failed_step,
            "verification_command": self.verification_command,
            "observed_output": self.observed_output,
            "expected_output": self.expected_output,
            "correction": self.correction,
            "actor": self.actor,
            "recorded_at": self.recorded_at,
            "details": self.details,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> ErrorLedgerEntry:
        if not isinstance(data, dict):
            raise ErrorLedgerError("Data must be a dictionary")
        
        required_fields = ("id", "objective_version_id", "error_category", "misconception", "actor", "recorded_at")
        for field in required_fields:
            val = data.get(field)
            if val is None or not isinstance(val, str) or not val.strip():
                raise ErrorLedgerError(f"Missing or invalid required field: {field}")

        category = data["error_category"]
        if category not in EXTENDED_ERROR_TAXONOMY:
            raise ErrorLedgerError(
                f"Invalid error_category: {category!r}. Must be one of {sorted(EXTENDED_ERROR_TAXONOMY)}"
            )

        details = data.get("details", {})
        if not isinstance(details, dict):
            raise ErrorLedgerError("details must be a dictionary")

        return cls(
            id=data["id"],
            objective_version_id=data["objective_version_id"],
            attempt_id=data.get("attempt_id"),
            error_category=category,
            misconception=data["misconception"],
            failed_step=data.get("failed_step"),
            verification_command=data.get("verification_command"),
            observed_output=data.get("observed_output"),
            expected_output=data.get("expected_output"),
            correction=data.get("correction"),
            actor=data["actor"],
            recorded_at=data["recorded_at"],
            details=details,
        )


def record_error(
    db: sqlite3.Connection,
    *,
    objective_version_id: str,
    error_category: str,
    misconception: str,
    failed_step: Optional[str] = None,
    verification_command: Optional[str] = None,
    observed_output: Optional[str] = None,
    expected_output: Optional[str] = None,
    correction: Optional[str] = None,
    attempt_id: Optional[str] = None,
    actor: str = "user-provided",
    details: Optional[Dict[str, Any]] = None,
    recorded_at: Optional[str] = None,
) -> ErrorLedgerEntry:
    """Record one explicit error/misconception with provenance and audit trail.

    Persists to the `error_ledger` table and writes an audit event to maintain
    full provenance.
    """
    if not objective_version_id or not isinstance(objective_version_id, str):
        raise ErrorLedgerError("objective_version_id must be a non-empty string")
    if error_category not in EXTENDED_ERROR_TAXONOMY:
        raise ErrorLedgerError(
            f"Invalid error_category: {error_category!r}. Must be one of {sorted(EXTENDED_ERROR_TAXONOMY)}"
        )
    if not misconception or not isinstance(misconception, str):
        raise ErrorLedgerError("misconception must be a non-empty string")
    if not actor or not isinstance(actor, str):
        raise ErrorLedgerError("actor must be a non-empty string")

    error_id = f"err:{uuid.uuid4().hex[:12]}"
    ts = recorded_at or now_iso()
    details_dict = dict(details) if details else {}

    db.execute(
        """
        INSERT INTO error_ledger (
            id, objective_version_id, attempt_id, error_category,
            misconception, failed_step, verification_command,
            observed_output, expected_output, correction,
            actor, recorded_at, details_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            error_id,
            objective_version_id,
            attempt_id,
            error_category,
            misconception,
            failed_step,
            verification_command,
            observed_output,
            expected_output,
            correction,
            actor,
            ts,
            json.dumps(details_dict, sort_keys=True),
        ),
    )

    # Insert audit event for provenance tracking
    db.execute(
        """
        INSERT INTO audit_event (
            namespace, event_type, target_ref, actor, occurred_at, details_json
        ) VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            "learning",
            "ACADEMIA_ERROR_RECORDED",
            error_id,
            actor,
            ts,
            json.dumps(
                {
                    "objective_version_id": objective_version_id,
                    "attempt_id": attempt_id,
                    "error_category": error_category,
                    "misconception": misconception,
                },
                sort_keys=True,
            ),
        ),
    )

    return ErrorLedgerEntry(
        id=error_id,
        objective_version_id=objective_version_id,
        attempt_id=attempt_id,
        error_category=error_category,
        misconception=misconception,
        failed_step=failed_step,
        verification_command=verification_command,
        observed_output=observed_output,
        expected_output=expected_output,
        correction=correction,
        actor=actor,
        recorded_at=ts,
        details=details_dict,
    )


def list_errors(
    db: sqlite3.Connection,
    *,
    objective_version_id: Optional[str] = None,
    error_category: Optional[str] = None,
    limit: int = 100,
) -> List[ErrorLedgerEntry]:
    """Retrieve error ledger entries filtered by objective or category."""
    query = "SELECT * FROM error_ledger WHERE 1=1"
    params: List[Any] = []

    if objective_version_id:
        query += " AND objective_version_id = ?"
        params.append(objective_version_id)
    if error_category:
        query += " AND error_category = ?"
        params.append(error_category)

    query += " ORDER BY recorded_at DESC, id DESC LIMIT ?"
    params.append(max(1, limit))

    rows = db.execute(query, params).fetchall()
    entries = []
    for row in rows:
        details_raw = row["details_json"] if "details_json" in row.keys() else "{}"
        try:
            details = json.loads(details_raw) if details_raw else {}
        except Exception:
            details = {}

        entries.append(
            ErrorLedgerEntry(
                id=row["id"],
                objective_version_id=row["objective_version_id"],
                attempt_id=row["attempt_id"],
                error_category=row["error_category"],
                misconception=row["misconception"],
                failed_step=row["failed_step"],
                verification_command=row["verification_command"],
                observed_output=row["observed_output"],
                expected_output=row["expected_output"],
                correction=row["correction"],
                actor=row["actor"],
                recorded_at=row["recorded_at"],
                details=details,
            )
        )
    return entries


def get_error_summary(
    db: sqlite3.Connection,
    *,
    objective_version_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Summarize recorded errors and misconceptions."""
    entries = list_errors(db, objective_version_id=objective_version_id, limit=1000)
    by_category: Dict[str, int] = {}
    misconceptions: List[Dict[str, str]] = []
    failed_steps: List[str] = []

    for entry in entries:
        by_category[entry.error_category] = by_category.get(entry.error_category, 0) + 1
        misconceptions.append({
            "id": entry.id,
            "objective_version_id": entry.objective_version_id,
            "category": entry.error_category,
            "misconception": entry.misconception,
            "correction": entry.correction or "",
        })
        if entry.failed_step and entry.failed_step not in failed_steps:
            failed_steps.append(entry.failed_step)

    return {
        "total_errors": len(entries),
        "by_category": by_category,
        "misconceptions": misconceptions,
        "failed_steps": failed_steps,
    }
