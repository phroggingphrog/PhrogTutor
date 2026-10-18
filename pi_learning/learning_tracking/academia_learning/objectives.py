"""Course/objective/prerequisite graph reads (LEARNER_EVIDENCE namespace)."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from .models import CONFIRMATION_ACTORS, CONFIRMATION_ACTOR_PLACEHOLDERS


@dataclass(frozen=True)
class Objective:
    objective_version_id: str
    objective_id: str
    course_id: str
    definition: str


def list_courses(db: sqlite3.Connection) -> list[dict]:
    rows = db.execute("SELECT id, title, created_at FROM course ORDER BY id").fetchall()
    return [dict(r) for r in rows]


def get_course_state(db: sqlite3.Connection, course_id: str) -> dict:
    """Summary of what exists for one course: objectives, sources, last activity."""
    course = db.execute("SELECT id, title, created_at FROM course WHERE id = ?", (course_id,)).fetchone()
    if course is None:
        raise ValueError(f"no such course: {course_id}")
    objective_count = db.execute(
        "SELECT COUNT(*) FROM objective_version WHERE course_id = ?", (course_id,)
    ).fetchone()[0]
    source_count = db.execute(
        """
        SELECT COUNT(DISTINCT sv.id) FROM source_version sv
          JOIN reference_version rv ON rv.source_version_id = sv.id
         WHERE rv.course_id = ?
        """,
        (course_id,),
    ).fetchone()[0]
    return {
        "course_id": course["id"],
        "title": course["title"],
        "created_at": course["created_at"],
        "objective_count": objective_count,
        "indexed_source_count": source_count,
    }


def list_objectives(db: sqlite3.Connection, course_id: str) -> list[Objective]:
    rows = db.execute(
        "SELECT id, objective_id, course_id, definition FROM objective_version "
        "WHERE course_id = ? ORDER BY rowid",
        (course_id,),
    ).fetchall()
    return [
        Objective(
            objective_version_id=r["id"],
            objective_id=r["objective_id"],
            course_id=r["course_id"],
            definition=r["definition"],
        )
        for r in rows
    ]


def get_learning_objective(db: sqlite3.Connection, objective_version_id: str) -> Objective | None:
    row = db.execute(
        "SELECT id, objective_id, course_id, definition FROM objective_version WHERE id = ?",
        (objective_version_id,),
    ).fetchone()
    if row is None:
        return None
    return Objective(
        objective_version_id=row["id"],
        objective_id=row["objective_id"],
        course_id=row["course_id"],
        definition=row["definition"],
    )


def get_concept_prerequisites(db: sqlite3.Connection, objective_version_id: str) -> list[str]:
    rows = db.execute(
        "SELECT prerequisite_version_id FROM prerequisite WHERE objective_version_id = ?",
        (objective_version_id,),
    ).fetchall()
    return [r["prerequisite_version_id"] for r in rows]


def is_prerequisite_satisfied(db: sqlite3.Connection, prerequisite_version_id: str) -> bool:
    """A prerequisite is satisfied only by an explicit, confirmed ACCEPT
    decision on its most recent mastery_evaluation with proposed_state
    'solid' -- semantic dependency satisfaction, never a raw count and
    never inferred from the mere existence of an attempt.
    """
    row = db.execute(
        f"""
        SELECT me.proposed_state, md.decision
          FROM mastery_evaluation me
          JOIN mastery_decision md ON md.id = (
               SELECT MAX(id) FROM mastery_decision
                WHERE evaluation_id = me.id AND actor IN ({CONFIRMATION_ACTOR_PLACEHOLDERS})
          )
         WHERE me.objective_version_id = ?
         ORDER BY me.created_at DESC, me.rowid DESC
         LIMIT 1
        """,
        (*sorted(CONFIRMATION_ACTORS), prerequisite_version_id),
    ).fetchone()
    if row is None:
        return False
    return row["decision"] == "ACCEPT" and row["proposed_state"] == "solid"


def unmet_prerequisites(db: sqlite3.Connection, objective_version_id: str) -> list[str]:
    return [
        p
        for p in get_concept_prerequisites(db, objective_version_id)
        if not is_prerequisite_satisfied(db, p)
    ]
