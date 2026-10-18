"""Demonstrated-confidence derivation from existing assessment evidence.

"Demonstrated confidence" means the confidence label attached to the most
recent human-confirmed assessment of an objective -- never a Canvas
completion percentage, never a raw attempt count, and never inferred from
chat/explanation/retrieval activity.
"""

from __future__ import annotations

import json
import sqlite3

from .models import (
    CONFIDENCE_ORDINAL,
    CONFIRMATION_ACTORS,
    CONFIRMATION_ACTOR_PLACEHOLDERS,
)


def latest_confidence_label(db: sqlite3.Connection, objective_version_id: str) -> str | None:
    """Most recent confidence label for this objective, or None if no
    assessment exists yet (== UNKNOWN, not zero -- see models.py).
    """
    row = db.execute(
        f"""
        SELECT a.criterion_scores_json
          FROM assessment a
          JOIN attempt at ON at.id = a.attempt_id
          JOIN exercise_version ev ON ev.id = at.exercise_version_id
          JOIN exercise_objective eo ON eo.exercise_version_id = ev.id
          JOIN mastery_evaluation me
            ON json_extract(me.evidence_refs_json, '$.assessment_id') = a.id
          JOIN mastery_decision md ON md.id = (
               SELECT MAX(latest.id) FROM mastery_decision latest
                WHERE latest.evaluation_id = me.id AND latest.actor IN ({CONFIRMATION_ACTOR_PLACEHOLDERS})
          )
         WHERE eo.objective_version_id = ?
           AND md.decision = 'ACCEPT'
         ORDER BY at.submitted_at DESC, at.rowid DESC
         LIMIT 1
        """,
        (*sorted(CONFIRMATION_ACTORS), objective_version_id),
    ).fetchone()
    if row is None:
        return None
    scores = json.loads(row["criterion_scores_json"])
    label = scores.get("confidence")
    if label not in CONFIDENCE_ORDINAL:
        return None
    return label


def confidence_ordinal(db: sqlite3.Connection, objective_version_id: str) -> int | None:
    label = latest_confidence_label(db, objective_version_id)
    return CONFIDENCE_ORDINAL.get(label) if label else None


def get_recent_attempts(db: sqlite3.Connection, objective_version_id: str, limit: int = 5) -> list[dict]:
    rows = db.execute(
        """
        SELECT at.id AS attempt_id, at.response_ref, at.assistance_level, at.submitted_at,
               a.id AS assessment_id, a.criterion_scores_json, a.evidence_refs_json
          FROM attempt at
          JOIN exercise_version ev ON ev.id = at.exercise_version_id
          JOIN exercise_objective eo ON eo.exercise_version_id = ev.id
          LEFT JOIN assessment a ON a.attempt_id = at.id AND a.supersedes_id IS NULL
         WHERE eo.objective_version_id = ?
         ORDER BY at.submitted_at DESC, at.rowid DESC
         LIMIT ?
        """,
        (objective_version_id, limit),
    ).fetchall()
    out = []
    for r in rows:
        entry = {
            "attempt_id": r["attempt_id"],
            "response_ref": r["response_ref"],
            "assistance_level": r["assistance_level"],
            "submitted_at": r["submitted_at"],
            "assessment_id": r["assessment_id"],
        }
        if r["criterion_scores_json"]:
            entry["criterion_scores"] = json.loads(r["criterion_scores_json"])
        out.append(entry)
    return out
