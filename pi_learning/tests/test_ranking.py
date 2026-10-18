from datetime import datetime, timezone
from pathlib import Path

import pytest

from learning_tracking.examples import Example
from learning_tracking.models import Assignment
from learning_tracking.planner import build_plan, rank_assignments


def assignment(identifier, deadline, prerequisites=(), progress=0):
    return Assignment(
        id=identifier,
        title=identifier,
        course="test",
        due_at=deadline,
        personal_final=deadline,
        prerequisites=tuple(prerequisites),
        progress=progress,
    )


def test_ranking_defaults_to_deadline_then_id_without_fake_proxies():
    """Without real prerequisite/confidence lookups, Canvas's raw
    ``prerequisites`` count and ``progress`` percentage must NOT be used
    as substitutes -- ranking degrades honestly to (deadline, id).
    """
    overdue = assignment("overdue", datetime(2026, 9, 9, tzinfo=timezone.utc))
    less_work = assignment("less-work", datetime(2026, 9, 11, tzinfo=timezone.utc), ["one"], 0.1)
    more_done = assignment("more-done", datetime(2026, 9, 11, tzinfo=timezone.utc), ["one", "two"], 0.8)
    priority = assignment("priority", datetime(2026, 9, 11, tzinfo=timezone.utc), ["one", "two"], 0.2)

    ranked = rank_assignments([less_work, more_done, overdue, priority])

    # Same deadline for the three non-overdue items -> alphabetical id
    # tiebreak, regardless of their (unused) prerequisite count/progress.
    assert [item.id for item in ranked] == ["overdue", "less-work", "more-done", "priority"]


def test_ranking_uses_real_prerequisite_and_confidence_lookups_when_supplied():
    """When a caller supplies real semantic lookups (as
    ``academia_learning.selection`` does against learning.sqlite3), the
    order is deadline -> missing prerequisite -> lowest confidence -> id,
    not raw Canvas fields."""
    same_deadline = datetime(2026, 9, 11, tzinfo=timezone.utc)
    blocked = assignment("blocked", same_deadline)  # prerequisite not satisfied
    low_confidence = assignment("low-confidence", same_deadline)
    high_confidence = assignment("high-confidence", same_deadline)

    unmet = {"blocked"}
    confidence = {"low-confidence": 1.0, "high-confidence": 3.0}

    ranked = rank_assignments(
        [high_confidence, low_confidence, blocked],
        prerequisite_satisfied=lambda a: a.id not in unmet,
        demonstrated_confidence=lambda a: confidence.get(a.id),
    )

    assert [item.id for item in ranked] == ["blocked", "low-confidence", "high-confidence"]


@pytest.mark.parametrize(
    ("mode", "execution_allowed"),
    [
        ("start-learning", True),
        ("start-assignment", True),
        ("start-brainstorm", False),
    ],
)
def test_planner_returns_one_complete_block(mode, execution_allowed):
    deadline = datetime(2026, 9, 16, tzinfo=timezone.utc)
    item = Assignment(
        "essay",
        "History essay",
        "History",
        deadline,
        deadline,
        ("source analysis", "argument structure"),
    )
    example = Example(Path("example.py"), "def analyze_source(source): ...", 2)

    plan = build_plan(item, mode=mode, example=example)

    assert "options" not in plan
    assert plan["mode"] == mode
    assert plan["execution_allowed"] is execution_allowed
    assert set(plan["block"]) == {
        "reason",
        "concepts",
        "real_example",
        "learner_problem",
        "artifact_check",
        "understanding_test",
        "continuation",
    }
    assert plan["block"]["concepts"] == [
        "source analysis", "argument structure", "history", "essay",
    ]
    assert plan["block"]["real_example"]["path"] == "example.py"


@pytest.mark.parametrize(
    ("course", "title", "expected"),
    [
        (
            "Database Concepts",
            "Relational Schema Assessment",
            [
                "database", "sql", "schema", "table", "key", "constraint",
                "join", "trigger", "audit", "provenance", "relational",
            ],
        ),
        (
            "Analysis of Categorical Data",
            "Categorical Case Studies",
            [
                "categorical", "classification", "outcome", "logistic", "odds",
                "count", "model", "aic", "diagnostic", "regression",
            ],
        ),
        (
            "Applied Bayesian Statistics",
            "Bayesian Calibration Assignment",
            [
                "bayesian", "prior", "posterior", "mcmc", "jags", "uncertainty",
                "calibration", "regression",
            ],
        ),
    ],
)
def test_planner_uses_course_profile_and_non_generic_title_terms(course, title, expected):
    deadline = datetime(2026, 9, 16, tzinfo=timezone.utc)
    item = Assignment("profile", title, course, deadline, deadline)

    plan = build_plan(item, mode="start-learning", example=None)

    assert plan["block"]["concepts"] == expected


def test_planner_problem_and_artifact_name_the_example_purpose():
    deadline = datetime(2026, 9, 16, tzinfo=timezone.utc)
    item = Assignment(
        "categorical",
        "Categorical Case Studies",
        "Analysis of Categorical Data",
        deadline,
        deadline,
    )
    example = Example(Path("analytics_ops.py"), "classify outcomes", 5)

    block = build_plan(item, mode="start-learning", example=example)["block"]

    assert "analytics ops" in block["learner_problem"].lower()
    assert "analytics ops" in block["artifact_check"].lower()


def test_planner_matches_profile_when_canvas_appends_a_course_code():
    deadline = datetime(2026, 9, 16, tzinfo=timezone.utc)
    item = Assignment(
        "categorical",
        "Case Studies Assessment",
        "Analysis of Categorical Data (2650)",
        deadline,
        deadline,
    )

    concepts = build_plan(item, mode="start-learning", example=None)["block"]["concepts"]

    assert concepts[0:3] == ["categorical", "classification", "outcome"]
