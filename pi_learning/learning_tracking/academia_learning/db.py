"""Connection handling and additive schema migration for learning.sqlite3.

Reuses the exact connection convention already established by
``agent/native_learning.py`` in hermes-core-dev (URI-mode ro/rw, foreign
keys on, busy timeout) so this package behaves identically to the existing
writer under concurrent access. This module does not import
``native_learning`` (keeps this package's blast radius independent of
hermes-core-dev); it reimplements the same, small, already-proven
connection pattern against the same file.
"""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

DEFAULT_DB_PATH = Path(os.environ.get("PI_LEARNING_DB", "/home/phroggingphrog/brain/stores/learning/learning.sqlite3"))  # pi-tutor: store moved to ~/brain/stores/learning

# Tables this package requires to already exist (owned by the pre-existing
# native learning schema). If any is missing, the database is not the
# expected store and every operation must fail closed rather than silently
# create a parallel schema.
_REQUIRED_TABLES = {
    "course",
    "source_version",
    "reference_version",
    "chunk",
    "chunk_fts",
    "objective_version",
    "prerequisite",
    "rubric_version",
    "exercise_version",
    "exercise_objective",
    "exercise_reference",
    "attempt",
    "assessment",
    "mastery_evaluation",
    "mastery_decision",
    "audit_event",
}

# Additive migration only: one new table for explicit spaced-retrieval
# review events. Nothing pre-existing is altered or dropped.
_REVIEW_EVENT_SCHEMA = """
CREATE TABLE IF NOT EXISTS review_event(
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    objective_version_id TEXT NOT NULL REFERENCES objective_version(id),
    attempt_id          TEXT REFERENCES attempt(id),
    reviewed_at         TEXT NOT NULL,
    rating              TEXT NOT NULL CHECK (rating IN ('AGAIN','HARD','GOOD','EASY')),
    verified_result      TEXT NOT NULL CHECK (verified_result IN ('CORRECT','PARTIAL','INCORRECT','UNKNOWN')),
    assistance_used     INTEGER NOT NULL DEFAULT 0,
    prior_interval_days REAL,
    next_interval_days  REAL NOT NULL,
    next_review_at      TEXT NOT NULL,
    scheduler_version   TEXT NOT NULL,
    -- Serialized fsrs.Card state (JSON via Card.to_json()) so the NEXT
    -- review can deserialize and continue FSRS scheduling from exactly
    -- where this one left off. NULL only for pre-FSRS rows, if any.
    card_state_json     TEXT,
    evidence_refs_json  TEXT NOT NULL,
    created_at          TEXT NOT NULL
)
"""
_REVIEW_EVENT_INDEX = """
CREATE INDEX IF NOT EXISTS idx_review_event_objective
    ON review_event(objective_version_id, next_review_at)
"""
_ERROR_LEDGER_SCHEMA = """
CREATE TABLE IF NOT EXISTS error_ledger (
    id                      TEXT PRIMARY KEY,
    objective_version_id    TEXT NOT NULL,
    attempt_id              TEXT,
    error_category          TEXT NOT NULL CHECK (error_category IN (
        'structural', 'deviation', 'application', 'metacognitive',
        'syntax', 'logic', 'runtime', 'verification_failed'
    )),
    misconception           TEXT NOT NULL,
    failed_step             TEXT,
    verification_command    TEXT,
    observed_output         TEXT,
    expected_output         TEXT,
    correction              TEXT,
    actor                   TEXT NOT NULL,
    recorded_at             TEXT NOT NULL,
    details_json            TEXT NOT NULL DEFAULT '{}'
);
"""
_ERROR_LEDGER_INDEX_OBJECTIVE = """
CREATE INDEX IF NOT EXISTS idx_error_ledger_objective
    ON error_ledger(objective_version_id, recorded_at);
"""
_ERROR_LEDGER_INDEX_CATEGORY = """
CREATE INDEX IF NOT EXISTS idx_error_ledger_category
    ON error_ledger(error_category);
"""
_LEARNING_EVENT_SCHEMA = """
CREATE TABLE IF NOT EXISTS learning_event(
    id                      TEXT PRIMARY KEY,
    source                  TEXT NOT NULL,
    sensitivity             TEXT NOT NULL,
    whole_system            TEXT NOT NULL,
    subsystem               TEXT NOT NULL,
    concept                 TEXT NOT NULL,
    incident_or_change_ref  TEXT,
    concept_tags_json       TEXT NOT NULL,
    observed_fact           TEXT NOT NULL,
    hypothesis              TEXT,
    model_explanation       TEXT,
    evidence_json           TEXT NOT NULL,
    lesson_json             TEXT NOT NULL,
    exercise_json           TEXT NOT NULL,
    verified_result         TEXT,
    verification_json       TEXT NOT NULL,
    mastery                 TEXT NOT NULL CHECK (mastery IN ('pending', 'solid', 'weak', 'reviewed')),
    sanitized_case_summary  INTEGER NOT NULL DEFAULT 0,
    created_at              TEXT NOT NULL
);
"""
_LEARNING_EVENT_ADDITIVE_COLUMNS = {
    # SQLite cannot add a NOT NULL column without a default.  Legacy rows
    # therefore receive explicit 'legacy' backfill values below; newly-created
    # tables use the strict NOT NULL/no-default schema above.
    "whole_system": "TEXT",
    "subsystem": "TEXT",
    "concept": "TEXT",
    "incident_or_change_ref": "TEXT",
    "hypothesis": "TEXT",
    "model_explanation": "TEXT",
    "verified_result": "TEXT",
    "sanitized_case_summary": "INTEGER NOT NULL DEFAULT 0",
}


class SchemaMismatchError(RuntimeError):
    """The database at the expected path is missing required tables."""


def _validate_schema(db: sqlite3.Connection) -> None:
    actual = {
        row[0]
        for row in db.execute("SELECT name FROM sqlite_master WHERE type IN ('table','view')")
    }
    missing = _REQUIRED_TABLES - actual
    if missing:
        raise SchemaMismatchError(
            "learning.sqlite3 is missing required tables: " + ", ".join(sorted(missing))
        )


def connect(path: Path | str | None = None, *, write: bool = False) -> sqlite3.Connection:
    """Open the learning store. Fails closed if the file or schema is wrong.

    ``write=False`` opens strictly read-only (sqlite3 URI ``mode=ro``); a
    missing file raises ``sqlite3.OperationalError`` rather than silently
    creating an empty database, matching the fail-closed requirement.
    """
    resolved = Path(path) if path is not None else DEFAULT_DB_PATH
    mode = "rw" if write else "ro"
    # Always a non-strict resolve (normalizes the path without requiring
    # the file to exist yet) -- a missing file must fail via sqlite3's own
    # URI mode=ro open (raises sqlite3.OperationalError), not via
    # pathlib.resolve(strict=True) raising a different exception type
    # before sqlite3 even gets a chance to fail closed consistently.
    uri = f"{resolved.expanduser().resolve(strict=False).as_uri()}?mode={mode}"
    db = sqlite3.connect(uri, uri=True, timeout=5.0)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys = ON")
    db.execute("PRAGMA busy_timeout = 5000")
    _validate_schema(db)
    return db


def ensure_review_event_schema(path: Path | str | None = None) -> None:
    """Idempotently create the additive ``review_event`` table if absent."""
    db = connect(path, write=True)
    try:
        db.execute("BEGIN IMMEDIATE")
        db.execute(_REVIEW_EVENT_SCHEMA)
        db.execute(_REVIEW_EVENT_INDEX)
        db.commit()
    finally:
        db.close()

def ensure_learning_event_schema(path: Path | str | None = None) -> None:
    """Create or migrate the ``learning_event`` table.

    Old stores may have populated rows without hierarchy columns.  SQLite
    cannot add strict NOT NULL columns to such a table, so migration adds
    nullable columns and explicitly marks those historical rows as ``legacy``.
    New tables use the strict schema above and cannot insert hierarchy-less
    events.
    """
    db = connect(path, write=True)
    try:
        db.execute("BEGIN IMMEDIATE")
        db.execute(_LEARNING_EVENT_SCHEMA)
        existing = {row[1] for row in db.execute("PRAGMA table_info(learning_event)")}
        missing_hierarchy = {
            column for column in ("whole_system", "subsystem", "concept") if column not in existing
        }
        for column, definition in _LEARNING_EVENT_ADDITIVE_COLUMNS.items():
            if column not in existing:
                db.execute(f"ALTER TABLE learning_event ADD COLUMN {column} {definition}")

        # SQLite cannot add strict NOT NULL columns to a populated table.
        # Rows from a schema that lacked hierarchy columns get an explicit
        # legacy marker; rows that already had those columns are untouched.
        if missing_hierarchy:
            db.execute(
                """
                UPDATE learning_event
                SET whole_system = CASE
                        WHEN whole_system IS NULL OR trim(whole_system) = '' THEN 'legacy'
                        ELSE whole_system END,
                    subsystem = CASE
                        WHEN subsystem IS NULL OR trim(subsystem) = '' THEN 'legacy'
                        ELSE subsystem END,
                    concept = CASE
                        WHEN concept IS NULL OR trim(concept) = '' THEN 'legacy'
                        ELSE concept END
                """
            )
        db.execute("CREATE INDEX IF NOT EXISTS idx_learning_event_source ON learning_event(source)")
        db.execute("CREATE INDEX IF NOT EXISTS idx_learning_event_mastery ON learning_event(mastery)")
        db.commit()
    finally:
        db.close()

def ensure_error_ledger_schema(path: Path | str | None = None) -> None:
    """Idempotently create the additive ``error_ledger`` table if absent."""
    db = connect(path, write=True)
    try:
        db.execute("BEGIN IMMEDIATE")
        db.execute(_ERROR_LEDGER_SCHEMA)
        db.execute(_ERROR_LEDGER_INDEX_OBJECTIVE)
        db.execute(_ERROR_LEDGER_INDEX_CATEGORY)
        db.commit()
    finally:
        db.close()


def now_iso() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()
