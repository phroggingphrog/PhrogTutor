"""Append-only learner-evidence writes.

Write chain, exactly as specified: attempt -> assessment -> evaluation
proposal -> human confirmation/rejection -> later review event. Nothing in
this module ever inserts a `mastery_decision` row automatically --
``append_attempt``/``propose_evaluation`` only ever leave a PENDING
proposal; only the separate, explicit ``confirm_evaluation`` call (with a
named, non-model actor) can accept or reject one. Raw attempts are never
rewritten; a correction is always a new row that references the one it
supersedes.
"""

from __future__ import annotations

import json
import sqlite3
import uuid

from . import objectives as obj_mod
from .db import now_iso
from .models import (
    AttemptReceipt,
    CONFIRMATION_ACTORS,
    CONFIRMATION_ACTOR_PLACEHOLDERS,
)

# Kept identical to the rubric already seeded by native_learning.py's
# seed_acha_profile() so both writers describe evidence the same way.
_RUBRIC_ID = "acha-profile-rubric:v1"
_RUBRIC_DIMENSIONS = ["status", "confidence", "fragility", "evidence", "provenance"]

_CONFIDENCE_INT_TO_LABEL = {0: "low", 1: "low", 2: "moderate", 3: "high"}


class EvidenceWriteError(RuntimeError):
    pass


def _ensure_rubric(db: sqlite3.Connection) -> str:
    row = db.execute("SELECT id FROM rubric_version WHERE id = ?", (_RUBRIC_ID,)).fetchone()
    if row is not None:
        return _RUBRIC_ID
    db.execute(
        "INSERT INTO rubric_version(id, rubric_id, version, criteria_json) VALUES (?,?,?,?)",
        (_RUBRIC_ID, "acha-profile-rubric", 1, json.dumps({"dimensions": _RUBRIC_DIMENSIONS})),
    )
    return _RUBRIC_ID


def _ensure_exercise_version(
    db: sqlite3.Connection, objective: obj_mod.Objective, exercise_version_id: str | None
) -> str:
    if exercise_version_id:
        row = db.execute(
            "SELECT id FROM exercise_version WHERE id = ?", (exercise_version_id,)
        ).fetchone()
        if row is None:
            raise EvidenceWriteError(f"no such exercise_version: {exercise_version_id}")
        return exercise_version_id

    concept = objective.objective_id.rsplit(":", 1)[-1]
    new_id = f"academia:evidence:{concept}:{uuid.uuid4().hex[:8]}"
    rubric_id = _ensure_rubric(db)
    db.execute(
        """
        INSERT INTO exercise_version(id, exercise_id, version, prompt, rubric_version_id, origin, review_state)
        VALUES (?,?,?,?,?,?,?)
        """,
        (
            new_id,
            new_id,
            1,
            f"Academia learning block for {objective.definition}",
            rubric_id,
            "academia-start-learning",
            "agent-generated-pending-review",
        ),
    )
    db.execute(
        "INSERT INTO exercise_objective(exercise_version_id, objective_version_id) VALUES (?,?)",
        (new_id, objective.objective_version_id),
    )
    return new_id


def append_attempt(
    db: sqlite3.Connection,
    *,
    objective_version_id: str,
    response_ref: str,
    assistance_level: str,
    checks: list[dict],
    errors: list[str],
    corrections: list[str],
    confidence_before: int | None,
    confidence_after: int,
    source_refs: list[str],
    next_action: str,
    exercise_version_id: str | None = None,
    supersedes_assessment_id: str | None = None,
    actor: str = "user-provided",
) -> AttemptReceipt:
    """Append one attempt + assessment + evaluation-proposal + audit event.

    This is the ONLY write path for a learning attempt. It never inserts a
    mastery_decision -- the resulting evaluation is PENDING_CONFIRMATION
    until a separate ``confirm_evaluation`` call.
    """
    if not (0 <= confidence_after <= 3):
        raise ValueError("confidence_after must be 0-3")
    if confidence_before is not None and not (0 <= confidence_before <= 3):
        raise ValueError("confidence_before must be 0-3 or None")

    objective = obj_mod.get_learning_objective(db, objective_version_id)
    if objective is None:
        raise EvidenceWriteError(f"no such objective_version: {objective_version_id}")

    ev_id = _ensure_exercise_version(db, objective, exercise_version_id)
    if supersedes_assessment_id is not None:
        prior = db.execute(
            """SELECT a.id FROM assessment a
                 JOIN attempt at ON at.id = a.attempt_id
                 JOIN exercise_objective eo ON eo.exercise_version_id = at.exercise_version_id
                WHERE a.id = ? AND eo.objective_version_id = ?
                  AND NOT EXISTS (
                      SELECT 1 FROM assessment newer WHERE newer.supersedes_id = a.id
                  )""",
            (supersedes_assessment_id, objective_version_id),
        ).fetchone()
        if prior is None:
            raise EvidenceWriteError(
                "superseded assessment must be the current assessment for this objective"
            )

    attempt_id = f"academia:attempt:{objective.objective_id.rsplit(':', 1)[-1]}:{uuid.uuid4().hex[:12]}"
    submitted_at = now_iso()
    db.execute(
        "INSERT INTO attempt(id, exercise_version_id, response_ref, assistance_level, submitted_at) "
        "VALUES (?,?,?,?,?)",
        (attempt_id, ev_id, response_ref, assistance_level, submitted_at),
    )

    assessment_id = f"academia:assessment:{attempt_id}"
    label_after = _CONFIDENCE_INT_TO_LABEL[confidence_after]
    criterion_scores = {
        "confidence": label_after,
        "confidence_int": confidence_after,
        "confidence_before_int": confidence_before,
    }
    evidence_refs = {
        "checks": checks,
        "errors": errors,
        "corrections": corrections,
        "source_refs": source_refs,
        "next_action": next_action,
        "provenance": actor,
    }
    db.execute(
        "INSERT INTO assessment(id, attempt_id, rubric_version_id, criterion_scores_json, "
        "evidence_refs_json, supersedes_id) VALUES (?,?,?,?,?,?)",
        (
            assessment_id,
            attempt_id,
            _ensure_rubric(db),
            json.dumps(criterion_scores, sort_keys=True),
            json.dumps(evidence_refs, sort_keys=True),
            supersedes_assessment_id,
        ),
    )

    proposed_state = "corrected" if corrections else ("solid" if confidence_after == 3 and not errors else "reinforce")
    evaluation_id = propose_evaluation(
        db,
        objective_version_id=objective_version_id,
        assessment_id=assessment_id,
        proposed_state=proposed_state,
        evidence_refs=[f"attempt:{attempt_id}", f"assessment:{assessment_id}", *source_refs],
    )

    audit_cur = db.execute(
        "INSERT INTO audit_event(namespace, event_type, target_ref, actor, occurred_at, details_json) "
        "VALUES (?,?,?,?,?,?)",
        (
            "learning",
            "ACADEMIA_ATTEMPT_APPENDED",
            attempt_id,
            actor,
            submitted_at,
            json.dumps(
                {
                    "objective_version_id": objective_version_id,
                    "assessment_id": assessment_id,
                    "evaluation_id": evaluation_id,
                    "confidence_before": confidence_before,
                    "confidence_after": confidence_after,
                },
                sort_keys=True,
            ),
        ),
    )

    return AttemptReceipt(
        attempt_id=attempt_id,
        exercise_version_id=ev_id,
        assessment_id=assessment_id,
        evaluation_id=evaluation_id,
        audit_event_id=audit_cur.lastrowid,
        objective_version_id=objective_version_id,
        confidence_after=confidence_after,
        status="PENDING_CONFIRMATION",
    )


def propose_evaluation(
    db: sqlite3.Connection,
    *,
    objective_version_id: str,
    assessment_id: str | None,
    proposed_state: str,
    evidence_refs: list[str],
    policy_version: str = "academia-learning-v1",
) -> str:
    """Insert an evidence-linked mastery_evaluation. Never inserts a
    mastery_decision -- the proposal stays PENDING_CONFIRMATION until
    ``confirm_evaluation`` is called by a named, non-model actor.
    """
    if proposed_state not in {"solid", "reinforce", "corrected", "fragile"}:
        raise ValueError(f"unknown proposed_state: {proposed_state}")
    evaluation_id = f"academia:evaluation:{uuid.uuid4().hex[:16]}"
    created_at = now_iso()
    details = {"assessment_id": assessment_id, "evidence_refs": evidence_refs}
    db.execute(
        "INSERT INTO mastery_evaluation(id, objective_version_id, policy_version, "
        "evidence_refs_json, proposed_state, evidence_count, created_at) VALUES (?,?,?,?,?,?,?)",
        (
            evaluation_id,
            objective_version_id,
            policy_version,
            json.dumps(details, sort_keys=True),
            proposed_state,
            len(evidence_refs),
            created_at,
        ),
    )
    return evaluation_id


def confirm_evaluation(db: sqlite3.Connection, evaluation_id: str, *, actor: str, decision: str) -> int:
    """Explicitly confirm or reject a pending evaluation proposal.

    ``actor`` MUST be one of the non-model actor types; a model cannot
    confirm its own proposal (there is no 'model'/'agent' actor accepted
    here -- this is the boundary enforced in code, not just prose).
    """
    if actor not in CONFIRMATION_ACTORS:
        raise ValueError(
            f"invalid confirmation actor {actor!r}; must be one of {sorted(CONFIRMATION_ACTORS)}"
        )
    if decision not in {"ACCEPT", "REJECT"}:
        raise ValueError(f"invalid decision: {decision}")
    row = db.execute("SELECT id FROM mastery_evaluation WHERE id = ?", (evaluation_id,)).fetchone()
    if row is None:
        raise EvidenceWriteError(f"no such evaluation: {evaluation_id}")
    cur = db.execute(
        "INSERT INTO mastery_decision(evaluation_id, decision, actor, occurred_at) VALUES (?,?,?,?)",
        (evaluation_id, decision, actor, now_iso()),
    )
    return cur.lastrowid


def read_progress(db: sqlite3.Connection, objective_version_id: str) -> dict:
    """Everything needed for a NEW session to resume correctly: the most
    recent attempt, its evaluation status, and whether it is confirmed.
    """
    attempt = db.execute(
        """
        SELECT at.id AS attempt_id, at.submitted_at, at.response_ref
          FROM attempt at
          JOIN exercise_version ev ON ev.id = at.exercise_version_id
          JOIN exercise_objective eo ON eo.exercise_version_id = ev.id
         WHERE eo.objective_version_id = ?
         ORDER BY at.submitted_at DESC, at.rowid DESC LIMIT 1
        """,
        (objective_version_id,),
    ).fetchone()
    evaluation = db.execute(
        f"""
        SELECT me.id AS evaluation_id, me.proposed_state, me.created_at,
               md.decision, md.actor AS decided_by, md.occurred_at AS decided_at
          FROM mastery_evaluation me
          LEFT JOIN mastery_decision md ON md.id = (
               SELECT MAX(id) FROM mastery_decision
                WHERE evaluation_id = me.id AND actor IN ({CONFIRMATION_ACTOR_PLACEHOLDERS})
          )
         WHERE me.objective_version_id = ?
         ORDER BY me.created_at DESC, me.rowid DESC LIMIT 1
        """,
        (*sorted(CONFIRMATION_ACTORS), objective_version_id),
    ).fetchone()
    return {
        "objective_version_id": objective_version_id,
        "last_attempt": dict(attempt) if attempt else None,
        "last_evaluation": dict(evaluation) if evaluation else None,
        "confirmed": bool(evaluation and evaluation["decision"] == "ACCEPT"),
    }
