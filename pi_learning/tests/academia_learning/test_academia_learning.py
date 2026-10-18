from __future__ import annotations

import json
import sqlite3

import pytest

from learning_tracking.academia_learning import api
from learning_tracking.academia_learning import selection as selection_mod
from learning_tracking.academia_learning.citations import (
    CitationValidationError,
    quarantine_invalid_source_versions,
    search_course_sources,
    validate_and_read_chunk,
)
from learning_tracking.academia_learning.db import connect
from learning_tracking.academia_learning.objectives import is_prerequisite_satisfied


def test_selection_prioritizes_unmet_prerequisite_over_confidence(db_path, canvas_summary_path):
    """integrity-checks has an unmet prerequisite... but relational-basics
    IS its prerequisite and is already solid/confirmed, so integrity-checks
    itself is the one with nothing blocking it -- the real blocked one is
    sql-ddl (depends on integrity-checks, which is only 'reinforce' and
    unconfirmed). Confirms stage-2 (prerequisite) fires correctly, using
    semantic confirmation, not a raw prerequisite count.
    """
    db = connect(db_path, write=False)
    try:
        block = selection_mod.select_next_block(db, canvas_summary_path=canvas_summary_path)
    finally:
        db.close()
    # sql-ddl's prerequisite (integrity-checks) is not confirmed solid ->
    # stage 2 fires for sql-ddl before stage 3 (confidence) ever
    # considers normalisation (which has zero evidence at all).
    assert block.selected_reason == "PREREQUISITE"
    assert block.objective_version_id == "db:sql-ddl:v1"
    assert block.unmet_prerequisites == ("db:integrity-checks:v1",)


def test_deadline_protection_starts_seven_days_before_personal_target(
    db_path, canvas_summary_path
):
    from learning_tracking.academia_learning.deadlines import nearest_indexed_deadline

    db = connect(db_path, write=False)
    try:
        assert nearest_indexed_deadline(
            db, canvas_summary_path, now="2026-09-08T13:59:58Z"
        ) is None
        hit = nearest_indexed_deadline(
            db, canvas_summary_path, now="2026-09-09T13:59:59Z"
        )
    finally:
        db.close()

    assert hit is not None
    assert hit[0].personal_final.isoformat() == "2026-09-16T13:59:59+00:00"


def test_prerequisite_satisfaction_is_semantic_not_a_raw_count(db_path):
    db = connect(db_path, write=False)
    try:
        # relational-basics has an explicit ACCEPT + proposed_state=solid.
        assert is_prerequisite_satisfied(db, "db:relational-basics:v1") is True
        # integrity-checks has evidence but NO mastery_decision at all --
        # must be treated as unsatisfied, not "attempted therefore fine".
        assert is_prerequisite_satisfied(db, "db:integrity-checks:v1") is False
        # never assessed at all.
        assert is_prerequisite_satisfied(db, "db:normalisation:v1") is False
    finally:
        db.close()


def test_no_evidence_ranks_ahead_of_low_but_is_never_a_fabricated_zero(db_path):
    """normalisation has zero assessments (UNKNOWN). Once the sql-ddl
    prerequisite gap is resolved (out of scope here), UNKNOWN must sort
    ahead of a demonstrated 'moderate'/'low' -- but the returned
    confidence_before must be None, never a fabricated 0.
    """
    db = connect(db_path, write=False)
    try:
        from learning_tracking.academia_learning.confidence import confidence_ordinal

        assert confidence_ordinal(db, "db:normalisation:v1") is None
        assert confidence_ordinal(db, "db:integrity-checks:v1") is None
        assert confidence_ordinal(db, "db:relational-basics:v1") == 3  # high
    finally:
        db.close()


def test_citation_validation_passes_for_untampered_source(db_path):
    db = connect(db_path, write=False)
    try:
        citation = validate_and_read_chunk(db, "chunk_relational_basics_0")
    finally:
        db.close()
    assert citation.freshness_state == "FRESH"
    assert "relation is a set of tuples" in citation.exact_text
    assert citation.byte_start == len(
        "# Relational Basics\n".encode("utf-8")
    )


def test_superseded_citations_require_explicit_history(db_path, source_file):
    from learning_tracking.academia_learning import ingestion

    source_file.write_text(
        "# History Test\nA relation has tuples.\n",
        encoding="utf-8",
    )
    db = connect(db_path, write=True)
    try:
        db.execute("BEGIN IMMEDIATE")
        old_version = ingestion.add_source_version(
            db,
            course_id="database-concepts",
            source_id="history-test.md",
            file_path=source_file,
            chunks=[ingestion.ChunkSpec(start_line=2, end_line=2)],
        )
        old_chunk = db.execute(
            "SELECT id FROM chunk WHERE source_version_id = ?", (old_version,)
        ).fetchone()[0]
        source_file.write_text(
            "# History Test\nA relation has tuples and attributes.\n",
            encoding="utf-8",
        )
        new_version = ingestion.add_source_version(
            db,
            course_id="database-concepts",
            source_id="history-test.md",
            file_path=source_file,
            chunks=[ingestion.ChunkSpec(start_line=2, end_line=2)],
        )
        db.commit()
    finally:
        db.close()

    db = connect(db_path, write=False)
    try:
        with pytest.raises(CitationValidationError, match="superseded"):
            validate_and_read_chunk(db, old_chunk)
        old = validate_and_read_chunk(
            db, old_chunk, allow_superseded=True
        )
        results = search_course_sources(
            db, "database-concepts", "relation", limit=5
        )
    finally:
        db.close()

    assert old.freshness_state == "SUPERSEDED"
    assert {item.source_version for item in results} == {new_version}


def test_citation_validation_fails_closed_on_tampered_file(db_path, source_file):
    # Tamper with the live file after the DB recorded its hash.
    source_file.write_text("# Relational Basics\nTAMPERED CONTENT.\n", encoding="utf-8")
    db = connect(db_path, write=False)
    try:
        with pytest.raises(CitationValidationError):
            validate_and_read_chunk(db, "chunk_relational_basics_0")
    finally:
        db.close()


def test_citation_validation_fails_closed_on_missing_file(db_path, source_file):
    source_file.unlink()
    db = connect(db_path, write=False)
    try:
        with pytest.raises(CitationValidationError):
            validate_and_read_chunk(db, "chunk_relational_basics_0")
    finally:
        db.close()


def test_invalid_current_source_can_be_quarantined_append_only(db_path, source_file):
    source_file.unlink()
    db = connect(db_path, write=True)
    try:
        assert quarantine_invalid_source_versions(db) == 1
        assert quarantine_invalid_source_versions(db) == 0
        tombstone = db.execute(
            "SELECT processing_status,supersedes_id FROM source_version WHERE processing_status='QUARANTINED'"
        ).fetchone()
        event = db.execute(
            "SELECT event_type FROM audit_event WHERE event_type='LEARNING_SOURCE_QUARANTINED'"
        ).fetchone()
    finally:
        db.close()

    assert tuple(tombstone) == ("QUARANTINED", "sv_relational_basics")
    assert event[0] == "LEARNING_SOURCE_QUARANTINED"


def test_citation_validation_fails_closed_on_out_of_bounds_citation_location(db_path, source_file):
    """citation_location pointing past the end of the live file must
    fail closed rather than silently returning a truncated/wrong excerpt.
    """
    writer = sqlite3.connect(db_path)
    writer.execute(
        "UPDATE chunk SET citation_location = ? WHERE id = ?",
        ("relational-basics.md:50-60", "chunk_relational_basics_0"),
    )
    writer.commit()
    writer.close()

    db = connect(db_path, write=False)
    try:
        with pytest.raises(CitationValidationError):
            validate_and_read_chunk(db, "chunk_relational_basics_0")
    finally:
        db.close()


def test_start_block_returns_validated_citations(db_path, canvas_summary_path, monkeypatch):
    result = api.start_block(db_path=db_path, canvas_summary_path=canvas_summary_path)
    assert result["objective_version_id"]
    assert result["minutes"] == 25
    for citation in result["citations"]:
        assert citation["freshness_state"] in {"FRESH", "SUPERSEDED"}
        assert citation["content_hash"]


def test_append_attempt_creates_exactly_one_new_row_set_and_leaves_old_rows_untouched(db_path):
    db = connect(db_path, write=False)
    try:
        before_attempts = db.execute("SELECT COUNT(*) FROM attempt").fetchone()[0]
        before_assessments = db.execute("SELECT COUNT(*) FROM assessment").fetchone()[0]
        old_row = db.execute(
            "SELECT response_ref FROM attempt WHERE id = 'at:relational-basics:1'"
        ).fetchone()
    finally:
        db.close()

    receipt = api.append_attempt(
        objective_version_id="db:normalisation:v1",
        response_ref="protected-ref:normalisation:session1",
        assistance_level="user-provided",
        confidence_before=None,
        confidence_after=2,
        errors=["forgot 3NF anomaly example"],
        corrections=["walked through the anomaly with a concrete table"],
        source_refs=["learning:relational-basics.md:v1"],
        next_action="attempt a 2NF-vs-3NF decomposition exercise next session",
        db_path=db_path,
    )

    assert receipt["status"] == "PENDING_CONFIRMATION"

    db = connect(db_path, write=False)
    try:
        after_attempts = db.execute("SELECT COUNT(*) FROM attempt").fetchone()[0]
        after_assessments = db.execute("SELECT COUNT(*) FROM assessment").fetchone()[0]
        unchanged_row = db.execute(
            "SELECT response_ref FROM attempt WHERE id = 'at:relational-basics:1'"
        ).fetchone()
        confirmed = db.execute(
            "SELECT decision FROM mastery_decision WHERE evaluation_id = ?",
            (receipt["evaluation_id"],),
        ).fetchone()
    finally:
        db.close()

    assert after_attempts == before_attempts + 1
    assert after_assessments == before_assessments + 1
    assert unchanged_row == old_row  # nothing pre-existing was rewritten
    assert confirmed is None  # unconfirmed model evaluation is NOT an accepted decision


def test_unconfirmed_evaluation_cannot_satisfy_a_prerequisite(db_path):
    receipt = api.append_attempt(
        objective_version_id="db:normalisation:v1",
        response_ref="ref:1",
        assistance_level="user-provided",
        confidence_before=None,
        confidence_after=3,
        next_action="n/a",
        db_path=db_path,
    )
    db = connect(db_path, write=True)
    try:
        db.execute(
            "INSERT INTO mastery_decision(evaluation_id, decision, actor, occurred_at) VALUES (?,?,?,?)",
            (receipt["evaluation_id"], "ACCEPT", "hermes-learning", "2026-09-21T00:00:00Z"),
        )
        db.commit()
        # Even a confidence_after=3 ("high") attempt must NOT count as a
        # satisfied prerequisite until a human/deterministic actor confirms.
        assert is_prerequisite_satisfied(db, "db:normalisation:v1") is False
    finally:
        db.close()

    api.confirm_evaluation(
        evaluation_id=receipt["evaluation_id"], actor="user-provided", decision="ACCEPT", db_path=db_path
    )
    # Confirmed, but proposed_state was "reinforce" (has no errors but
    # confidence 3 alone -> "solid" per append_attempt's own state rule
    # when there are no errors) -- re-check reflects the real stored state.
    db = connect(db_path, write=False)
    try:
        assert is_prerequisite_satisfied(db, "db:normalisation:v1") is True
    finally:
        db.close()


def test_confirm_evaluation_rejects_model_actor(db_path):
    receipt = api.append_attempt(
        objective_version_id="db:normalisation:v1",
        response_ref="ref:1",
        assistance_level="user-provided",
        confidence_before=None,
        confidence_after=1,
        next_action="n/a",
        db_path=db_path,
    )
    with pytest.raises(ValueError):
        api.confirm_evaluation(
            evaluation_id=receipt["evaluation_id"], actor="model", decision="ACCEPT", db_path=db_path
        )


def test_legacy_user_provenance_actor_remains_authoritative(db_path):
    receipt = api.append_attempt(
        objective_version_id="db:normalisation:v1",
        response_ref="ref:legacy-provenance",
        assistance_level="user-provided",
        confidence_before=None,
        confidence_after=3,
        next_action="n/a",
        db_path=db_path,
    )
    api.confirm_evaluation(
        evaluation_id=receipt["evaluation_id"],
        actor="user-provided 2026-09-10",
        decision="ACCEPT",
        db_path=db_path,
    )

    assert api.read_progress(
        objective_version_id="db:normalisation:v1", db_path=db_path
    )["confirmed"] is True


def test_confidence_uses_only_confirmed_assessments_and_keeps_correction_lineage(db_path):
    from learning_tracking.academia_learning.confidence import confidence_ordinal

    first = api.append_attempt(
        objective_version_id="db:normalisation:v1",
        response_ref="ref:first",
        assistance_level="user-provided",
        confidence_before=None,
        confidence_after=3,
        next_action="verify",
        db_path=db_path,
    )
    db = connect(db_path, write=False)
    try:
        assert confidence_ordinal(db, "db:normalisation:v1") is None
    finally:
        db.close()
    api.confirm_evaluation(
        evaluation_id=first["evaluation_id"],
        actor="user-provided",
        decision="ACCEPT",
        db_path=db_path,
    )

    correction = api.append_attempt(
        objective_version_id="db:normalisation:v1",
        response_ref="ref:correction",
        assistance_level="user-provided",
        confidence_before=3,
        confidence_after=1,
        corrections=["corrected the earlier score"],
        next_action="review",
        supersedes_assessment_id=first["assessment_id"],
        db_path=db_path,
    )
    db = connect(db_path, write=False)
    try:
        assert confidence_ordinal(db, "db:normalisation:v1") == 3
        assert db.execute(
            "SELECT supersedes_id FROM assessment WHERE id = ?",
            (correction["assessment_id"],),
        ).fetchone()[0] == first["assessment_id"]
    finally:
        db.close()


def test_cross_session_resume_reads_back_the_appended_attempt(db_path):
    receipt = api.append_attempt(
        objective_version_id="db:normalisation:v1",
        response_ref="ref:session-a",
        assistance_level="user-provided",
        confidence_before=None,
        confidence_after=1,
        next_action="continue with a worked example",
        db_path=db_path,
    )

    # A brand new "session" only has the objective id -- simulate that by
    # opening a fresh connection/call with no in-memory state carried over.
    progress = api.retrieve_evidence("db:normalisation:v1", db_path=db_path)

    assert progress["last_attempt"]["attempt_id"] == receipt["attempt_id"]
    assert progress["confirmed"] is False


def test_record_review_is_explicit_and_never_fabricated(db_path):
    result = api.record_review(
        objective_version_id="db:relational-basics:v1",
        attempt_id="at:relational-basics:1",
        rating="GOOD",
        verified_result="CORRECT",
        db_path=db_path,
    )
    assert result["scheduler_version"] == "py-fsrs-scheduler-default-params-v1"
    assert result["prior_interval_days"] is None
    assert result["next_interval_days"] > 0
    assert result["fsrs_state"] in {"Learning", "Review", "Relearning"}

    db = connect(db_path, write=False)
    try:
        due = db.execute(
            "SELECT COUNT(*) FROM review_event WHERE objective_version_id = 'db:relational-basics:v1'"
        ).fetchone()[0]
    finally:
        db.close()
    assert due == 1


def test_no_review_event_means_unknown_not_due(db_path):
    """An objective never reviewed must not appear in get_due_reviews --
    confirms 'missing evidence is UNKNOWN, not failure or zero mastery'."""
    from learning_tracking.academia_learning.spaced_retrieval import get_due_reviews

    db = connect(db_path, write=False)
    try:
        due = get_due_reviews(db, "database-concepts")
    finally:
        db.close()
    assert due == []


def test_empty_course_is_never_selected_as_a_candidate(db_path):
    """empty-course has zero objective_version rows; the candidate-course
    scan must skip it entirely rather than selecting from -- or
    fabricating an objective for -- a course with no indexed graph.
    """
    db = connect(db_path, write=False)
    try:
        candidates = selection_mod._candidate_courses(db)
    finally:
        db.close()
    assert "empty-course" not in candidates
    assert "database-concepts" in candidates


def test_connect_fails_closed_on_missing_database(tmp_path):
    missing = tmp_path / "does-not-exist.sqlite3"
    with pytest.raises(sqlite3.OperationalError):
        connect(missing, write=False)


def test_connect_rejects_wrong_schema(tmp_path):
    wrong = tmp_path / "wrong.sqlite3"
    conn = sqlite3.connect(wrong)
    conn.execute("CREATE TABLE unrelated(id INTEGER)")
    conn.commit()
    conn.close()
    from learning_tracking.academia_learning.db import SchemaMismatchError

    with pytest.raises(SchemaMismatchError):
        connect(wrong, write=False)
