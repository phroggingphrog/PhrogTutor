"""Protected-deadline resolution, bridging Canvas evidence to learning.sqlite3 course ids.

Reuses ``learning_tracking.canvas.load_assignments``: Canvas ``due_at`` already
includes the user's +14-day accommodation; derive ``original_due_at = due_at - 14
days`` and set ``personal_final = original_due_at``. Scheduling targets
``personal_final``; do not add another 14 days. The one real gap this module closes is the course-id
mismatch: Canvas assignment JSON carries a human ``course_name`` string
("Analysis of Categorical Data (2650)"), while learning.sqlite3 keys
objectives by a short slug ("categorical-analysis"). That mapping did not
exist anywhere before; ``resolve_course_id`` makes it explicit and
verifiable against the live course table instead of silently guessing.
"""

from __future__ import annotations

from datetime import datetime, timezone
import re
import sqlite3
from pathlib import Path

from ..canvas import load_assignments
from ..models import Assignment
DEFAULT_CANVAS_SUMMARY = Path(
    "/home/phroggingphrog/data/hermes/runtime/canvas_scan/course_materials/upcoming_assignments_summary.json"
)

# Explicit, verified mapping from the distinctive substring of Canvas's
# `course_name` to the learning.sqlite3 `course.id` slug. Built directly
# from the real upcoming_assignments_summary.json `core_courses` block
# (database_concepts/categorical_analysis/bayesian_statistics) cross-
# checked against the real `course` table rows -- not guessed. Extend
# this table, do not add fuzzy/heuristic matching, when a new course is
# indexed.
_CANVAS_NAME_TO_COURSE_ID = (
    ("Database Concepts", "database-concepts"),
    ("Analysis of Categorical Data", "categorical-analysis"),
    ("Categorical Data", "categorical-analysis"),
    ("Applied Bayesian Statistics", "bayesian-statistics"),
    ("Bayesian Statistics", "bayesian-statistics"),
)


def resolve_course_id(db: sqlite3.Connection, canvas_course_name: str) -> str | None:
    """Map a Canvas `course_name` string to a learning.sqlite3 course id.

    Returns None (not a guess) when no mapping entry matches or the
    resolved id does not actually exist in the live `course` table --
    callers must treat that as "no protected deadline applies to an
    indexed course" and fall through to the next selection stage rather
    than crash or fabricate a course.
    """
    normalized = re.sub(r"\s*\(\d+\)\s*$", "", canvas_course_name).strip()
    for needle, course_id in _CANVAS_NAME_TO_COURSE_ID:
        if needle.lower() in normalized.lower():
            row = db.execute("SELECT 1 FROM course WHERE id = ?", (course_id,)).fetchone()
            if row is not None:
                return course_id
            return None
    return None


def load_protected_deadlines(
    db: sqlite3.Connection, canvas_summary_path: Path | None = None
) -> list[tuple[Assignment, str | None]]:
    """Every upcoming Canvas assignment, nearest-personal-deadline first,
    paired with the learning.sqlite3 course id it resolves to (or None).
    """
    path = canvas_summary_path or DEFAULT_CANVAS_SUMMARY
    if not path.is_file():
        return []
    assignments = load_assignments(path)
    ordered = sorted(
        assignments,
        key=lambda a: (
            a.personal_final is None,
            a.personal_final if a.personal_final is not None else datetime.max.replace(tzinfo=timezone.utc),
        ),
    )
    return [(a, resolve_course_id(db, a.course)) for a in ordered]


def nearest_indexed_deadline(
    db: sqlite3.Connection,
    canvas_summary_path: Path | None = None,
    *,
    now: str | None = None,
) -> tuple[Assignment, str] | None:
    """The nearest-personal-deadline Canvas assignment that resolves to a
    course id which ALSO has an objective graph indexed in learning.sqlite3
    (i.e. a course the selection cascade can actually act on). Returns
    None -- not a fabricated deadline -- if no upcoming assignment resolves
    to an indexed course.
    """
    current = (
        datetime.fromisoformat(now.replace("Z", "+00:00"))
        if now
        else datetime.now(timezone.utc)
    )
    for assignment, course_id in load_protected_deadlines(db, canvas_summary_path):
        if course_id is None:
            continue
        if assignment.protection_start is None or current < assignment.protection_start:
            continue
        if assignment.extended_due_at is not None and current > assignment.extended_due_at:
            continue
        has_objectives = db.execute(
            "SELECT 1 FROM objective_version WHERE course_id = ? LIMIT 1", (course_id,)
        ).fetchone()
        if has_objectives is not None:
            return assignment, course_id
    return None
