"""Isolated fixture DB for academia_learning tests.

Never touches the real personal database at
/home/phroggingphrog/data/hermes/db/learning.sqlite3 -- every test gets a
fresh, temp-file sqlite3 database built from the exact same schema
(copied verbatim from the live schema via `sqlite3 learning.sqlite3
.schema`) plus a handful of new-but-plausible seed rows and real temp
source files so content-hash/citation validation is genuinely exercised.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path

import pytest

from learning_tracking.academia_learning.db import (
    ensure_error_ledger_schema,
    ensure_learning_event_schema,
    ensure_review_event_schema,
)

SCHEMA_SQL = """
CREATE TABLE source_version (
    id TEXT PRIMARY KEY,
    namespace TEXT NOT NULL,
    source_id TEXT NOT NULL,
    native_version INTEGER NOT NULL,
    content_hash TEXT NOT NULL,
    private_locator TEXT NOT NULL,
    captured_at TEXT NOT NULL,
    processing_status TEXT NOT NULL,
    evidence_class TEXT NOT NULL DEFAULT 'SOURCE',
    supersedes_id TEXT REFERENCES source_version(id),
    UNIQUE(namespace, source_id, native_version)
);
CREATE TABLE chunk (
    id TEXT PRIMARY KEY,
    namespace TEXT NOT NULL,
    source_version_id TEXT NOT NULL REFERENCES source_version(id),
    record_ref TEXT NOT NULL,
    ordinal INTEGER NOT NULL CHECK (ordinal >= 0),
    citation_location TEXT NOT NULL,
    text TEXT NOT NULL,
    token_count INTEGER NOT NULL CHECK (token_count >= 0),
    tokenizer_version TEXT NOT NULL DEFAULT 'whitespace-v1',
    UNIQUE(source_version_id, ordinal)
);
CREATE VIRTUAL TABLE chunk_fts USING fts5(text, content='chunk', content_rowid='rowid');
CREATE TABLE audit_event (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    namespace TEXT NOT NULL,
    event_type TEXT NOT NULL,
    target_ref TEXT NOT NULL,
    actor TEXT NOT NULL,
    occurred_at TEXT NOT NULL,
    details_json TEXT NOT NULL
);
CREATE TABLE course(id TEXT PRIMARY KEY, title TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE reference_version(
    id TEXT PRIMARY KEY, source_version_id TEXT NOT NULL REFERENCES source_version(id),
    course_id TEXT NOT NULL REFERENCES course(id), created_at TEXT NOT NULL
);
CREATE TABLE objective_version(
    id TEXT PRIMARY KEY, objective_id TEXT NOT NULL, course_id TEXT NOT NULL REFERENCES course(id),
    version INTEGER NOT NULL, definition TEXT NOT NULL, UNIQUE(objective_id, version)
);
CREATE TABLE prerequisite(
    objective_version_id TEXT NOT NULL REFERENCES objective_version(id),
    prerequisite_version_id TEXT NOT NULL REFERENCES objective_version(id),
    PRIMARY KEY(objective_version_id, prerequisite_version_id)
);
CREATE TABLE rubric_version(
    id TEXT PRIMARY KEY, rubric_id TEXT NOT NULL, version INTEGER NOT NULL, criteria_json TEXT NOT NULL,
    UNIQUE(rubric_id, version)
);
CREATE TABLE exercise_version(
    id TEXT PRIMARY KEY, exercise_id TEXT NOT NULL, version INTEGER NOT NULL, prompt TEXT NOT NULL,
    rubric_version_id TEXT NOT NULL REFERENCES rubric_version(id), origin TEXT NOT NULL, review_state TEXT NOT NULL,
    UNIQUE(exercise_id, version)
);
CREATE TABLE exercise_objective(
    exercise_version_id TEXT NOT NULL REFERENCES exercise_version(id),
    objective_version_id TEXT NOT NULL REFERENCES objective_version(id),
    PRIMARY KEY(exercise_version_id, objective_version_id)
);
CREATE TABLE exercise_reference(
    exercise_version_id TEXT NOT NULL REFERENCES exercise_version(id),
    reference_version_id TEXT NOT NULL REFERENCES reference_version(id),
    citation_location TEXT NOT NULL,
    PRIMARY KEY(exercise_version_id, reference_version_id)
);
CREATE TABLE attempt(
    id TEXT PRIMARY KEY, exercise_version_id TEXT NOT NULL REFERENCES exercise_version(id),
    response_ref TEXT NOT NULL, assistance_level TEXT NOT NULL, submitted_at TEXT NOT NULL
);
CREATE TABLE assessment(
    id TEXT PRIMARY KEY, attempt_id TEXT NOT NULL REFERENCES attempt(id),
    rubric_version_id TEXT NOT NULL REFERENCES rubric_version(id),
    criterion_scores_json TEXT NOT NULL, evidence_refs_json TEXT NOT NULL,
    supersedes_id TEXT REFERENCES assessment(id)
);
CREATE TABLE mastery_evaluation(
    id TEXT PRIMARY KEY, objective_version_id TEXT NOT NULL REFERENCES objective_version(id),
    policy_version TEXT NOT NULL, evidence_refs_json TEXT NOT NULL, proposed_state TEXT NOT NULL,
    evidence_count INTEGER NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE mastery_decision(
    id INTEGER PRIMARY KEY AUTOINCREMENT, evaluation_id TEXT NOT NULL REFERENCES mastery_evaluation(id),
    decision TEXT NOT NULL, actor TEXT NOT NULL, occurred_at TEXT NOT NULL
);
"""


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


@pytest.fixture()
def fixture_workspace(tmp_path: Path) -> Path:
    return tmp_path


@pytest.fixture()
def source_file(fixture_workspace: Path) -> Path:
    p = fixture_workspace / "sources" / "relational-basics.md"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        "# Relational Basics\n"
        "A relation is a set of tuples sharing the same attributes.\n"
        "Keys uniquely identify a tuple within a relation.\n",
        encoding="utf-8",
    )
    return p


@pytest.fixture()
def db_path(fixture_workspace: Path, source_file: Path) -> Path:
    path = fixture_workspace / "learning.sqlite3"
    conn = sqlite3.connect(path)
    conn.executescript(SCHEMA_SQL)

    now = "2026-09-10T00:00:00+00:00"
    conn.execute(
        "INSERT INTO course(id, title, created_at) VALUES (?,?,?)",
        ("database-concepts", "Database Concepts", now),
    )
    conn.execute(
        "INSERT INTO course(id, title, created_at) VALUES (?,?,?)",
        ("empty-course", "Empty Course (no objectives yet)", now),
    )

    content = source_file.read_bytes()
    content_hash = _sha256(content)
    conn.execute(
        "INSERT INTO source_version(id, namespace, source_id, native_version, content_hash, "
        "private_locator, captured_at, processing_status, evidence_class, supersedes_id) "
        "VALUES (?,?,?,?,?,?,?,?,?,NULL)",
        (
            "sv_relational_basics",
            "learning",
            "relational-basics.md",
            1,
            content_hash,
            str(source_file),
            now,
            "PROCESSED",
            "SOURCE",
        ),
    )
    conn.execute(
        "INSERT INTO reference_version(id, source_version_id, course_id, created_at) VALUES (?,?,?,?)",
        ("ref_relational_basics", "sv_relational_basics", "database-concepts", now),
    )
    text = "A relation is a set of tuples sharing the same attributes.\n"
    conn.execute(
        "INSERT INTO chunk(id, namespace, source_version_id, record_ref, ordinal, "
        "citation_location, text, token_count, tokenizer_version) VALUES (?,?,?,?,?,?,?,?,?)",
        (
            "chunk_relational_basics_0",
            "learning",
            "sv_relational_basics",
            "learning:relational-basics.md:v1",
            0,
            "relational-basics.md:2-2",
            text,
            len(text.split()),
            "whitespace-v1",
        ),
    )
    conn.execute("INSERT INTO chunk_fts(rowid, text) SELECT rowid, text FROM chunk")

    # Objective graph: relational-basics has no prerequisite and one
    # 'moderate' assessment; integrity-checks depends on relational-basics
    # and is NOT yet confirmed solid (prerequisite unmet); sql-ddl depends
    # on integrity-checks (transitively unreachable). normalisation has no
    # assessment at all (UNKNOWN, not zero).
    objectives = [
        ("db:relational-basics:v1", "db:relational-basics", "Relations, keys, tuples, and attributes."),
        ("db:integrity-checks:v1", "db:integrity-checks", "Entity, domain, and referential integrity checks."),
        ("db:sql-ddl:v1", "db:sql-ddl", "DDL for tables, keys, constraints."),
        ("db:normalisation:v1", "db:normalisation", "Functional dependencies and normal forms."),
    ]
    for oid, slug, definition in objectives:
        conn.execute(
            "INSERT INTO objective_version(id, objective_id, course_id, version, definition) VALUES (?,?,?,?,?)",
            (oid, slug, "database-concepts", 1, definition),
        )
    conn.execute(
        "INSERT INTO prerequisite(objective_version_id, prerequisite_version_id) VALUES (?,?)",
        ("db:integrity-checks:v1", "db:relational-basics:v1"),
    )
    conn.execute(
        "INSERT INTO prerequisite(objective_version_id, prerequisite_version_id) VALUES (?,?)",
        ("db:sql-ddl:v1", "db:integrity-checks:v1"),
    )

    conn.execute(
        "INSERT INTO rubric_version(id, rubric_id, version, criteria_json) VALUES (?,?,?,?)",
        ("fixture-rubric:v1", "fixture-rubric", 1, json.dumps({"dimensions": ["confidence"]})),
    )
    # relational-basics: one CONFIRMED-solid assessment (satisfies the
    # integrity-checks prerequisite check).
    conn.execute(
        "INSERT INTO exercise_version(id, exercise_id, version, prompt, rubric_version_id, origin, review_state) "
        "VALUES (?,?,?,?,?,?,?)",
        ("ex:relational-basics:v1", "ex:relational-basics", 1, "prior evidence", "fixture-rubric:v1", "fixture", "accepted"),
    )
    conn.execute(
        "INSERT INTO exercise_objective(exercise_version_id, objective_version_id) VALUES (?,?)",
        ("ex:relational-basics:v1", "db:relational-basics:v1"),
    )
    conn.execute(
        "INSERT INTO attempt(id, exercise_version_id, response_ref, assistance_level, submitted_at) VALUES (?,?,?,?,?)",
        ("at:relational-basics:1", "ex:relational-basics:v1", "prior evidence", "user-provided", now),
    )
    conn.execute(
        "INSERT INTO assessment(id, attempt_id, rubric_version_id, criterion_scores_json, evidence_refs_json, supersedes_id) "
        "VALUES (?,?,?,?,?,NULL)",
        (
            "as:relational-basics:1",
            "at:relational-basics:1",
            "fixture-rubric:v1",
            json.dumps({"confidence": "high"}),
            json.dumps({"evidence": "prior"}),
        ),
    )
    conn.execute(
        "INSERT INTO mastery_evaluation(id, objective_version_id, policy_version, evidence_refs_json, "
        "proposed_state, evidence_count, created_at) VALUES (?,?,?,?,?,?,?)",
        (
            "me:relational-basics:1",
            "db:relational-basics:v1",
            "fixture-v1",
            json.dumps({"assessment_id": "as:relational-basics:1"}),
            "solid",
            1,
            now,
        ),
    )
    conn.execute(
        "INSERT INTO mastery_decision(evaluation_id, decision, actor, occurred_at) VALUES (?,?,?,?)",
        ("me:relational-basics:1", "ACCEPT", "user-provided", now),
    )

    # integrity-checks: one moderate-confidence assessment, but NOT
    # confirmed (no mastery_decision at all) -- stays PENDING.
    conn.execute(
        "INSERT INTO exercise_version(id, exercise_id, version, prompt, rubric_version_id, origin, review_state) "
        "VALUES (?,?,?,?,?,?,?)",
        ("ex:integrity-checks:v1", "ex:integrity-checks", 1, "prior evidence", "fixture-rubric:v1", "fixture", "accepted"),
    )
    conn.execute(
        "INSERT INTO exercise_objective(exercise_version_id, objective_version_id) VALUES (?,?)",
        ("ex:integrity-checks:v1", "db:integrity-checks:v1"),
    )
    conn.execute(
        "INSERT INTO attempt(id, exercise_version_id, response_ref, assistance_level, submitted_at) VALUES (?,?,?,?,?)",
        ("at:integrity-checks:1", "ex:integrity-checks:v1", "prior evidence", "user-provided", now),
    )
    conn.execute(
        "INSERT INTO assessment(id, attempt_id, rubric_version_id, criterion_scores_json, evidence_refs_json, supersedes_id) "
        "VALUES (?,?,?,?,?,NULL)",
        (
            "as:integrity-checks:1",
            "at:integrity-checks:1",
            "fixture-rubric:v1",
            json.dumps({"confidence": "moderate"}),
            json.dumps({"evidence": "prior"}),
        ),
    )
    conn.execute(
        "INSERT INTO mastery_evaluation(id, objective_version_id, policy_version, evidence_refs_json, "
        "proposed_state, evidence_count, created_at) VALUES (?,?,?,?,?,?,?)",
        (
            "me:integrity-checks:1",
            "db:integrity-checks:v1",
            "fixture-v1",
            json.dumps({"assessment_id": "as:integrity-checks:1"}),
            "reinforce",
            1,
            now,
        ),
    )
    # Deliberately NOT confirmed -- stays PENDING_CONFIRMATION.

    conn.commit()
    conn.close()
    ensure_review_event_schema(path)
    ensure_learning_event_schema(path)
    ensure_error_ledger_schema(path)
    return path


@pytest.fixture()
def canvas_summary_path(fixture_workspace: Path) -> Path:
    p = fixture_workspace / "upcoming_assignments_summary.json"
    p.write_text(
        json.dumps(
            {
                "fetched_at": "2026-09-10T08:16:13+00:00",
                "upcoming": [
                    {
                        "course_id": "999",
                        "course_name": "Database Concepts (2650)",
                        "id": 1,
                        "name": "Weekly quiz",
                        "due_at": "2026-09-30T13:59:59Z",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    return p
