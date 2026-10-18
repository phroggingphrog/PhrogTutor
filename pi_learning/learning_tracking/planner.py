from datetime import datetime, timezone
from typing import Callable

from .concepts import concept_terms
from .examples import Example
from .models import Assignment


def rank_assignments(
    assignments: list[Assignment],
    *,
    prerequisite_satisfied: Callable[[Assignment], bool] | None = None,
    demonstrated_confidence: Callable[[Assignment], float | None] | None = None,
) -> list[Assignment]:
    """Rank by nearest personal target deadline, then (when real semantic
    lookups are supplied) missing-prerequisite-dependency, then lowest
    demonstrated confidence, then id as a stable tiebreak.

    Canvas's raw ``prerequisites`` count and ``progress`` percentage are
    NEVER used as substitutes for dependency satisfaction or demonstrated
    confidence -- neither field measures either concept. Without real
    ``prerequisite_satisfied``/``demonstrated_confidence`` lookups (this
    module has no database access of its own -- see
    ``learning_tracking.academia_learning`` for the DB-backed cascade),
    ranking degrades honestly to (personal target, id) rather than pretending
    an unrelated Canvas field is a dependency or confidence signal. Canvas's
    ``due_at`` is already the user's accommodated (+14 day) deadline; imported
    assignments set ``personal_final`` to the original due date, so ranking
    protects the accommodation buffer.
    """

    def key(item: Assignment):
        unmet = (
            not prerequisite_satisfied(item) if prerequisite_satisfied is not None else False
        )
        confidence = (
            demonstrated_confidence(item) if demonstrated_confidence is not None else None
        )
        confidence_rank = confidence if confidence is not None else -1.0

        deadline = item.personal_final or item.due_at
        if deadline is not None:
            norm_deadline = (
                deadline if deadline.tzinfo is not None else deadline.replace(tzinfo=timezone.utc)
            )
            deadline_order = (0, norm_deadline)
        else:
            deadline_order = (1, datetime.max.replace(tzinfo=timezone.utc))

        return (
            deadline_order,
            0 if unmet else 1,
            confidence_rank,
            item.id,
        )
    return sorted(assignments, key=key)


def build_plan(assignment: Assignment, *, mode: str, example: Example | None) -> dict:
    if mode not in {"start-learning", "start-assignment", "start-brainstorm"}:
        raise ValueError(f"unknown mode: {mode}")
    real_example = None
    purpose = None
    if example:
        purpose = example.path.stem.replace("_", " ").replace("-", " ")
        real_example = {
            "path": str(example.path),
            "excerpt": example.excerpt,
            "score": example.score,
        }
    return {
        "mode": mode,
        "execution_allowed": mode != "start-brainstorm",
        "block": {
            "reason": (
                f"{assignment.title} has a personal deadline of {assignment.personal_final.isoformat()}."
                if assignment.personal_final is not None
                else f"{assignment.title} has no scheduled deadline."
            ),
            "concepts": concept_terms(assignment.course, assignment.title, assignment.prerequisites),
            "real_example": real_example,
            "learner_problem": (
                f"Recreate the {purpose} workflow for {assignment.title}."
                if purpose else f"Apply the concepts directly to {assignment.title}."
            ),
            "artifact_check": (
                f"Produce one reviewable {purpose} artifact for {assignment.title} and check it against the example."
                if purpose else f"Produce one reviewable artifact for {assignment.title} and check it against the brief."
            ),
            "understanding_test": "Explain the key choice and one rejected alternative without notes.",
            "continuation": "Record the result, then continue with the next ranked assignment.",
        },
    }
