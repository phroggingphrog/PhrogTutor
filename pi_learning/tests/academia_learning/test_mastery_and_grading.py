from __future__ import annotations

from learning_tracking.academia_learning import api
from learning_tracking.academia_learning.db import connect
from learning_tracking.academia_learning.grading import (
    ERROR_TAXONOMY,
    grade_answer,
)
from learning_tracking.academia_learning.mastery import (
    GATE_THRESHOLD,
    compute_mastery,
)


def test_mastery_unknown_for_never_attempted_objective(db_path):
    db = connect(db_path, write=False)
    try:
        estimate = compute_mastery(db, "db:normalisation:v1")
    finally:
        db.close()
    assert estimate.attempt_count == 0
    assert estimate.gate_passed is False
    assert "UNKNOWN" in estimate.reason


def test_mastery_never_writes_to_the_database(db_path):
    """compute_mastery is read-only: opened via api.mastery_status (which
    uses a strict ro connection) must not raise and must not change any
    row.
    """
    before = api.retrieve_evidence("db:relational-basics:v1", db_path=db_path)
    result = api.mastery_status("db:relational-basics:v1", db_path=db_path)
    after = api.retrieve_evidence("db:relational-basics:v1", db_path=db_path)
    assert before == after
    assert result["gate_threshold"] == GATE_THRESHOLD
    assert "p_mastery" in result


def test_mastery_gate_requires_multiple_correct_attempts(db_path):
    """A single correct attempt must NOT be enough to pass the 0.85 gate
    -- the small-sample confidence cap (DeepTutor pattern) exists exactly
    to prevent one lucky answer from mastering an objective.
    """
    api.append_attempt(
        objective_version_id="db:normalisation:v1",
        response_ref="ref:1",
        assistance_level="user-provided",
        confidence_before=None,
        confidence_after=3,
        next_action="n/a",
        db_path=db_path,
    )
    result = api.mastery_status("db:normalisation:v1", db_path=db_path)
    assert result["attempt_count"] == 1
    assert result["gate_passed"] is False  # capped at 0.5 with n=1


def test_mastery_gate_can_pass_after_several_consistent_correct_attempts(db_path):
    for i in range(6):
        api.append_attempt(
            objective_version_id="db:normalisation:v1",
            response_ref=f"ref:{i}",
            assistance_level="user-provided",
            confidence_before=None,
            confidence_after=3,
            next_action="n/a",
            db_path=db_path,
        )
    result = api.mastery_status("db:normalisation:v1", db_path=db_path)
    assert result["attempt_count"] == 6
    assert result["gate_passed"] is True


def test_mastery_errors_recorded_on_an_attempt_prevent_passing(db_path):
    for i in range(6):
        api.append_attempt(
            objective_version_id="db:normalisation:v1",
            response_ref=f"ref:{i}",
            assistance_level="user-provided",
            confidence_before=None,
            confidence_after=3,
            errors=["misapplied the rule"],
            next_action="n/a",
            db_path=db_path,
        )
    result = api.mastery_status("db:normalisation:v1", db_path=db_path)
    assert result["gate_passed"] is False


def test_grade_exact_match():
    r = grade_answer("Paris", "paris")
    assert r.verdict == "CORRECT"
    assert r.method == "exact_match"


def test_grade_fuzzy_short_catches_typo():
    r = grade_answer("relaional basics", "relational basics")
    assert r.verdict == "CORRECT"
    assert r.method == "fuzzy_short"
    assert r.score >= 0.85


def test_grade_fuzzy_short_rejects_wrong_short_answer():
    r = grade_answer("integrity checks", "relational basics")
    assert r.verdict == "INCORRECT"
    assert r.method == "fuzzy_short"


def test_grade_keyword_density_for_open_answer():
    expected_keywords = ["primary key", "foreign key", "referential integrity"]
    response = (
        "A referential integrity constraint links a foreign key in one "
        "table to the primary key of another, ensuring the reference "
        "always points to an existing row."
    )
    r = grade_answer(response, "long form explanation not used directly", keywords=expected_keywords)
    assert r.verdict == "CORRECT"
    assert r.method == "keyword_density"


def test_grade_falls_back_to_needs_judgment_without_keywords():
    long_response = "x " * 20  # exceeds the fuzzy-short cutoff, no keywords supplied
    r = grade_answer(long_response, "a short expected answer that is different enough")
    assert r.verdict == "NEEDS_JUDGMENT"


def test_grade_via_api_wrapper_matches_direct_call():
    direct = grade_answer("Paris", "paris")
    via_api = api.grade("Paris", "paris")
    assert via_api["verdict"] == direct.verdict
    assert via_api["method"] == direct.method


def test_error_taxonomy_is_the_four_deeptutor_types():
    assert set(ERROR_TAXONOMY) == {"structural", "deviation", "application", "metacognitive"}
