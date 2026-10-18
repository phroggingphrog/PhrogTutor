"""Explicit, versioned spaced-retrieval scheduling using real FSRS.

This did not exist anywhere in the audited codebase before this module.
It uses the Free Spaced Repetition Scheduler algorithm via the
`open-spaced-repetition/py-fsrs` library (MIT license, the reference
Python implementation of FSRS -- https://github.com/open-spaced-repetition/py-fsrs),
the same algorithm family the original Academia learning contract names
("FSRS-style scheduling is permitted from explicit review events"),
rather than a hand-rolled approximation.

It only ever runs off ``review_event`` rows created by ``record_review()``
-- i.e. off an explicit rating tied to a real attempt -- and never off
chat/explanation/retrieval activity. An objective with zero review_event
rows is UNKNOWN/never-reviewed, not overdue and not mastered; it simply
does not appear in ``get_due_reviews`` until a first explicit review
event exists. Each objective's FSRS ``Card`` state is persisted
(``card_state_json``) and deserialized on its next review, so scheduling
is continuous and reproducible -- not recomputed from scratch each time.
"""

from __future__ import annotations

import json
import sqlite3

from fsrs import Card, Rating, Scheduler

from .db import now_iso

SCHEDULER_VERSION = "py-fsrs-scheduler-default-params-v1"

_RATING_TO_FSRS = {
    "AGAIN": Rating.Again,
    "HARD": Rating.Hard,
    "GOOD": Rating.Good,
    "EASY": Rating.Easy,
}

_scheduler = Scheduler()  # module-level: stateless besides its fixed parameters


def _load_card(db: sqlite3.Connection, objective_version_id: str) -> Card:
    row = db.execute(
        """
        SELECT card_state_json FROM review_event
         WHERE objective_version_id = ? AND card_state_json IS NOT NULL
         ORDER BY reviewed_at DESC, id DESC LIMIT 1
        """,
        (objective_version_id,),
    ).fetchone()
    if row is None:
        return Card()  # a fresh FSRS card, due immediately (matches "never reviewed")
    return Card.from_json(row["card_state_json"])


def record_review(
    db: sqlite3.Connection,
    *,
    objective_version_id: str,
    attempt_id: str | None,
    rating: str,
    verified_result: str,
    assistance_used: bool,
    evidence_refs: list[str],
) -> dict:
    """Append one explicit review event, scheduled by real FSRS.

    Never infers a rating or a result; both are required, explicit inputs
    from the caller (a human rating, or a deterministic-check outcome).
    """
    if rating not in _RATING_TO_FSRS:
        raise ValueError(f"invalid rating: {rating}")
    if verified_result not in {"CORRECT", "PARTIAL", "INCORRECT", "UNKNOWN"}:
        raise ValueError(f"invalid verified_result: {verified_result}")

    prior = db.execute(
        """
        SELECT next_interval_days FROM review_event
         WHERE objective_version_id = ?
         ORDER BY reviewed_at DESC, id DESC LIMIT 1
        """,
        (objective_version_id,),
    ).fetchone()
    prior_interval = prior["next_interval_days"] if prior else None

    card = _load_card(db, objective_version_id)
    new_card, review_log = _scheduler.review_card(card, _RATING_TO_FSRS[rating])

    reviewed_at = now_iso()
    next_review_at = new_card.due.isoformat()
    next_interval_days = (new_card.due - review_log.review_datetime).total_seconds() / 86400.0

    cur = db.execute(
        """
        INSERT INTO review_event(
            objective_version_id, attempt_id, reviewed_at, rating,
            verified_result, assistance_used, prior_interval_days,
            next_interval_days, next_review_at, scheduler_version,
            card_state_json, evidence_refs_json, created_at
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            objective_version_id,
            attempt_id,
            reviewed_at,
            rating,
            verified_result,
            1 if assistance_used else 0,
            prior_interval,
            next_interval_days,
            next_review_at,
            SCHEDULER_VERSION,
            new_card.to_json(),
            json.dumps(evidence_refs, sort_keys=True),
            reviewed_at,
        ),
    )
    return {
        "review_event_id": cur.lastrowid,
        "objective_version_id": objective_version_id,
        "reviewed_at": reviewed_at,
        "rating": rating,
        "verified_result": verified_result,
        "prior_interval_days": prior_interval,
        "next_interval_days": next_interval_days,
        "next_review_at": next_review_at,
        "scheduler_version": SCHEDULER_VERSION,
        "fsrs_state": new_card.state.name,
        "fsrs_stability": new_card.stability,
        "fsrs_difficulty": new_card.difficulty,
    }


def get_due_reviews(db: sqlite3.Connection, course_id: str, *, now: str | None = None) -> list[dict]:
    """Objectives in this course whose most recent review is due, i.e. has
    at least one explicit review_event AND next_review_at <= now. An
    objective that was never reviewed is intentionally absent from this
    list (UNKNOWN, not due -- see module docstring).
    """
    cutoff = now or now_iso()
    rows = db.execute(
        """
        SELECT re.objective_version_id, re.next_review_at, re.rating,
               re.scheduler_version
          FROM review_event re
          JOIN objective_version ov ON ov.id = re.objective_version_id
         WHERE ov.course_id = ?
           AND re.id = (
               SELECT MAX(id) FROM review_event
                WHERE objective_version_id = re.objective_version_id
           )
           AND re.next_review_at <= ?
         ORDER BY re.next_review_at ASC
        """,
        (course_id, cutoff),
    ).fetchall()
    return [dict(r) for r in rows]


def schedule_remediation_on_error(
    db: sqlite3.Connection,
    *,
    objective_version_id: str,
    error_id: str | None = None,
    attempt_id: str | None = None,
    misconception: str | None = None,
    rating: str = "AGAIN",
) -> dict:
    """Schedule immediate/accelerated spaced review for an objective after an error."""
    evidence_refs = [f"error:{error_id}"] if error_id else []
    if misconception:
        evidence_refs.append(f"misconception:{misconception}")
    return record_review(
        db,
        objective_version_id=objective_version_id,
        attempt_id=attempt_id,
        rating=rating,
        verified_result="INCORRECT",
        assistance_used=True,
        evidence_refs=evidence_refs,
    )
