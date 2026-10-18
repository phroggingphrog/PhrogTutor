"""The four-stage `start learning` selection cascade.

Order, exactly as specified: nearest protected assessment deadline ->
missing prerequisite dependency -> lowest demonstrated confidence -> due
spaced-retrieval item. Never falls back to raw prerequisite count or a
Canvas completion percentage.

Because only one course ("acha-profile", the Database-Concepts objective
graph) currently has any indexed objectives, stage 1 legitimately no-ops
whenever the nearest Canvas deadline points at a course with no objective
graph yet (see deadlines.nearest_indexed_deadline) -- that is correct,
disclosed behavior, not a silent skip: the returned reason_detail always
says which stage fired and why the earlier stages did not apply.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from . import learner_profile as profile_mod
from . import objectives as obj_mod
from . import spaced_retrieval as sr_mod
from .citations import search_course_sources
from .confidence import confidence_ordinal, latest_confidence_label
from .deadlines import nearest_indexed_deadline
from .models import Citation, LearningBlock
from .namespaces import Namespace


class NoCandidateError(RuntimeError):
    """No course in learning.sqlite3 has an objective graph to select from."""


def _candidate_courses(db: sqlite3.Connection) -> list[str]:
    rows = db.execute("SELECT DISTINCT course_id FROM objective_version ORDER BY course_id").fetchall()
    return [r["course_id"] for r in rows]


def _select_within_course(
    db: sqlite3.Connection, course_id: str, *, now: str | None = None
) -> tuple[obj_mod.Objective, str, str] | None:
    """Stages 2-4 applied within one course. Returns
    (objective, selected_reason, reason_detail) or None if the course has
    objectives but every one of them is already 'solid' with nothing due.
    """
    objectives = obj_mod.list_objectives(db, course_id)
    if not objectives:
        return None

    # Stage 2: missing prerequisite dependency. An objective whose own
    # prerequisites are unmet is prioritized over one that is merely
    # low-confidence, per "begin with the first missing prerequisite when
    # foundations are absent".
    for o in objectives:
        unmet = obj_mod.unmet_prerequisites(db, o.objective_version_id)
        if unmet:
            return (
                o,
                "PREREQUISITE",
                f"{o.objective_id} has an unmet prerequisite: {unmet[0]}.",
            )

    # Stage 3: lowest demonstrated confidence. Objectives never assessed
    # (ordinal None / UNKNOWN) are treated as needing attention first,
    # ahead of a demonstrated "low" -- see models.CONFIDENCE_ORDINAL.
    ranked = sorted(
        objectives,
        key=lambda o: (
            -1 if confidence_ordinal(db, o.objective_version_id) is None
            else confidence_ordinal(db, o.objective_version_id),
            o.objective_id,
        ),
    )
    lowest = ranked[0]
    lowest_ordinal = confidence_ordinal(db, lowest.objective_version_id)
    if lowest_ordinal is None:
        return (lowest, "CONFIDENCE", f"{lowest.objective_id} has no demonstrated evidence yet.")
    if lowest_ordinal < 3:  # not yet "high"
        label = latest_confidence_label(db, lowest.objective_version_id)
        return (lowest, "CONFIDENCE", f"{lowest.objective_id} demonstrated confidence is '{label}'.")

    # Stage 4: due spaced-retrieval item.
    due = sr_mod.get_due_reviews(db, course_id, now=now)
    if due:
        due_objective = obj_mod.get_learning_objective(db, due[0]["objective_version_id"])
        if due_objective:
            return (
                due_objective,
                "SPACED_RETRIEVAL",
                f"{due_objective.objective_id} review is due "
                f"(next_review_at={due[0]['next_review_at']}, "
                f"scheduler={due[0]['scheduler_version']}).",
            )

    return None


def select_next_block(
    db: sqlite3.Connection,
    *,
    canvas_summary_path: Path | None = None,
    now: str | None = None,
    citation_query_terms: int = 2,
) -> LearningBlock:
    """Run the full four-stage cascade and return exactly one block."""
    candidates = _candidate_courses(db)
    if not candidates:
        raise NoCandidateError("no course in learning.sqlite3 has an indexed objective graph")

    # Stage 1: nearest protected deadline, restricted to a course that has
    # an objective graph to act on.
    deadline_hit = nearest_indexed_deadline(db, canvas_summary_path, now=now)
    if deadline_hit is not None:
        assignment, course_id = deadline_hit
        picked = _select_within_course(db, course_id, now=now)
        if picked is not None:
            objective, stage_reason, detail = picked
            # The deadline stage selected WHICH COURSE is urgent; the
            # inner stage (PREREQUISITE/CONFIDENCE/SPACED_RETRIEVAL)
            # selected WHICH CONCEPT within it -- report the actual
            # decisive stage, never a hardcoded "DEADLINE" that would
            # misrepresent why THIS objective (as opposed to some other
            # one in the same course) was chosen. Deadline context is
            # still preserved in reason_detail either way.
            reason_detail = (
                f"Nearest protected deadline is {assignment.title} "
                f"(personal_final={assignment.personal_final.isoformat()}, "
                f"protection_start={assignment.protection_start.isoformat()}) "
                f"makes {course_id} the priority course; {detail}"
            )
            return _build_block(db, objective, stage_reason, reason_detail, citation_query_terms)

    # Stage 1 did not resolve to an actionable pick (no upcoming
    # assignment maps to an indexed course, or that course has nothing
    # left to select). Fall through to stages 2-4 across every indexed
    # course, in course-id order, first course with a pick wins.
    for course_id in candidates:
        picked = _select_within_course(db, course_id, now=now)
        if picked is not None:
            objective, stage_reason, detail = picked
            return _build_block(db, objective, stage_reason, detail, citation_query_terms)

    raise NoCandidateError(
        "every indexed objective is already 'solid' with no prerequisite gap and no due review"
    )


def _build_block(
    db: sqlite3.Connection,
    objective: obj_mod.Objective,
    selected_reason: str,
    reason_detail: str,
    citation_query_terms: int,
) -> LearningBlock:
    prerequisite_ids = obj_mod.get_concept_prerequisites(db, objective.objective_version_id)
    unmet = obj_mod.unmet_prerequisites(db, objective.objective_version_id)
    confidence = confidence_ordinal(db, objective.objective_version_id)

    exercise_row = db.execute(
        """
        SELECT ev.id FROM exercise_version ev
          JOIN exercise_objective eo ON eo.exercise_version_id = ev.id
         WHERE eo.objective_version_id = ?
         ORDER BY ev.rowid DESC LIMIT 1
        """,
        (objective.objective_version_id,),
    ).fetchone()
    exercise_version_id = exercise_row["id"] if exercise_row else None

    # University-knowledge citations: search the SAME course's indexed
    # sources for the objective's own definition text, bounded to <=2
    # results per the first-slice spec. Concept name/definition is real
    # DB content, not a fabricated query.
    concept_label = objective.objective_id.rsplit(":", 1)[-1]
    query = " OR ".join(concept_label.replace("-", " ").split()[:citation_query_terms]) or concept_label
    try:
        citations: list[Citation] = search_course_sources(
            db, objective.course_id, query, limit=2
        )
    except sqlite3.OperationalError:
        # FTS5 MATCH syntax error on an unusual concept label -- fail
        # closed on citations only, not on the whole block; INSUFFICIENT
        # citation evidence is disclosed via an empty tuple, never
        # fabricated.
        citations = []

    profile = profile_mod.get_learner_profile(db)
    course_constraints = profile.course_constraints if profile else ()
    allowed_adaptations = profile.allowed_adaptations() if profile else ()

    return LearningBlock(
        course_id=objective.course_id,
        objective_version_id=objective.objective_version_id,
        concept=concept_label,
        definition=objective.definition,
        selected_reason=selected_reason,
        reason_detail=reason_detail,
        prerequisite_ids=tuple(prerequisite_ids),
        unmet_prerequisites=tuple(unmet),
        confidence_before=confidence,
        citations=tuple(citations),
        exercise_version_id=exercise_version_id,
        course_constraints=course_constraints,
        allowed_adaptations=allowed_adaptations,
    )
