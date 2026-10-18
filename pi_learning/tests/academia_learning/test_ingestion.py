from __future__ import annotations

import pytest

from learning_tracking.academia_learning import ingestion
from learning_tracking.academia_learning.citations import validate_and_read_chunk
from learning_tracking.academia_learning.db import connect
from learning_tracking.academia_learning.objectives import get_learning_objective


def test_add_source_version_creates_validated_chunks(db_path, fixture_workspace):
    transcript = fixture_workspace / "lecture.md"
    transcript.write_text(
        "# Week 8 Lecture — Normal Forms\n"
        "A relation is in 3NF when every non-key attribute depends on the whole key.\n"
        "Functional dependencies drive normalisation decisions.\n",
        encoding="utf-8",
    )
    db = connect(db_path, write=True)
    try:
        db.execute("BEGIN IMMEDIATE")
        source_version_id = ingestion.add_source_version(
            db,
            course_id="database-concepts",
            source_id="week8-lecture-transcript",
            file_path=transcript,
            chunks=[ingestion.ChunkSpec(start_line=2, end_line=2)],
        )
        db.commit()
    finally:
        db.close()

    db = connect(db_path, write=False)
    try:
        chunk_row = db.execute(
            "SELECT id FROM chunk WHERE source_version_id = ?", (source_version_id,)
        ).fetchone()
        citation = validate_and_read_chunk(db, chunk_row["id"])
    finally:
        db.close()
    assert "3NF" in citation.exact_text
    assert citation.freshness_state == "FRESH"


def test_add_source_version_is_idempotent(db_path, fixture_workspace):
    transcript = fixture_workspace / "lecture2.md"
    transcript.write_text("Line one.\nLine two.\n", encoding="utf-8")
    db = connect(db_path, write=True)
    try:
        db.execute("BEGIN IMMEDIATE")
        first = ingestion.add_source_version(
            db,
            course_id="database-concepts",
            source_id="idempotent-test",
            file_path=transcript,
            chunks=[ingestion.ChunkSpec(start_line=1, end_line=1)],
        )
        before_count = db.execute("SELECT COUNT(*) FROM source_version").fetchone()[0]
        second = ingestion.add_source_version(
            db,
            course_id="database-concepts",
            source_id="idempotent-test",
            file_path=transcript,
            chunks=[ingestion.ChunkSpec(start_line=1, end_line=1)],
        )
        after_count = db.execute("SELECT COUNT(*) FROM source_version").fetchone()[0]
        db.commit()
    finally:
        db.close()
    assert first == second
    assert before_count == after_count  # no duplicate row on re-ingest


def test_add_source_version_appends_changed_content(db_path, fixture_workspace):
    transcript = fixture_workspace / "lecture-versioned.md"
    transcript.write_text("First edition.\n", encoding="utf-8")
    db = connect(db_path, write=True)
    try:
        db.execute("BEGIN IMMEDIATE")
        first = ingestion.add_source_version(
            db,
            course_id="database-concepts",
            source_id="versioned-test",
            file_path=transcript,
            chunks=[ingestion.ChunkSpec(start_line=1, end_line=1)],
        )
        transcript.write_text("Second edition.\n", encoding="utf-8")
        second = ingestion.add_source_version(
            db,
            course_id="database-concepts",
            source_id="versioned-test",
            file_path=transcript,
            chunks=[ingestion.ChunkSpec(start_line=1, end_line=1)],
        )
        row = db.execute(
            "SELECT native_version, supersedes_id FROM source_version WHERE id = ?",
            (second,),
        ).fetchone()
        record_ref = db.execute(
            "SELECT record_ref FROM chunk WHERE source_version_id = ?", (second,)
        ).fetchone()[0]
        db.commit()
    finally:
        db.close()

    assert second != first
    assert tuple(row) == (2, first)
    assert ":v2:" in record_ref


def test_add_source_version_rejects_unknown_course(db_path, fixture_workspace):
    transcript = fixture_workspace / "lecture3.md"
    transcript.write_text("content\n", encoding="utf-8")
    db = connect(db_path, write=True)
    try:
        db.execute("BEGIN IMMEDIATE")
        with pytest.raises(ingestion.IngestionError):
            ingestion.add_source_version(
                db,
                course_id="no-such-course",
                source_id="x",
                file_path=transcript,
                chunks=[ingestion.ChunkSpec(start_line=1, end_line=1)],
            )
    finally:
        db.rollback()
        db.close()


def test_add_source_version_rejects_out_of_bounds_chunk(db_path, fixture_workspace):
    transcript = fixture_workspace / "lecture4.md"
    transcript.write_text("only one line\n", encoding="utf-8")
    db = connect(db_path, write=True)
    try:
        db.execute("BEGIN IMMEDIATE")
        with pytest.raises(ingestion.IngestionError):
            ingestion.add_source_version(
                db,
                course_id="database-concepts",
                source_id="oob-test",
                file_path=transcript,
                chunks=[ingestion.ChunkSpec(start_line=1, end_line=5)],
            )
    finally:
        db.rollback()
        db.close()


def test_add_objective_creates_and_is_idempotent(db_path):
    db = connect(db_path, write=True)
    try:
        db.execute("BEGIN IMMEDIATE")
        first = ingestion.add_objective(
            db,
            course_id="database-concepts",
            objective_id="db:3nf-decomposition",
            definition="Decomposing a relation to satisfy third normal form.",
        )
        second = ingestion.add_objective(
            db,
            course_id="database-concepts",
            objective_id="db:3nf-decomposition",
            definition="Decomposing a relation to satisfy third normal form.",
        )
        db.commit()
    finally:
        db.close()
    assert first == second == "db:3nf-decomposition:v1"

    db = connect(db_path, write=False)
    try:
        obj = get_learning_objective(db, first)
    finally:
        db.close()
    assert obj is not None
    assert "third normal form" in obj.definition


def test_add_objective_with_real_prerequisite_link(db_path):
    db = connect(db_path, write=True)
    try:
        db.execute("BEGIN IMMEDIATE")
        objective_version_id = ingestion.add_objective(
            db,
            course_id="database-concepts",
            objective_id="db:advanced-normal-forms",
            definition="BCNF and beyond.",
            prerequisite_ids=["db:integrity-checks:v1"],  # exists in fixture DB
        )
        db.commit()
    finally:
        db.close()

    db = connect(db_path, write=False)
    try:
        row = db.execute(
            "SELECT prerequisite_version_id FROM prerequisite WHERE objective_version_id = ?",
            (objective_version_id,),
        ).fetchone()
    finally:
        db.close()
    assert row["prerequisite_version_id"] == "db:integrity-checks:v1"


def test_add_objective_rejects_fabricated_prerequisite(db_path):
    db = connect(db_path, write=True)
    try:
        db.execute("BEGIN IMMEDIATE")
        with pytest.raises(ingestion.IngestionError):
            ingestion.add_objective(
                db,
                course_id="database-concepts",
                objective_id="db:something-new",
                definition="...",
                prerequisite_ids=["db:does-not-exist:v1"],
            )
    finally:
        db.rollback()
        db.close()
