"""Deterministic mastery gating: Bayesian Knowledge Tracing (BKT) + a hard
progression gate.

Ported as a pattern from two independently reverse-engineered systems that
converge on the same idea (deterministic, LLM-free mastery arithmetic
gating progression):
- Tutor MCP (Go): standard BKT state {PMastery, PLearn, PForget, PSlip,
  PGuess}, unified gate threshold 0.85.
- DeepTutor (HKUDS, Python): recency-weighted accuracy with small-sample
  confidence caps ({1 attempt: cap 0.5, 2 attempts: cap 0.8}) so a learner
  cannot pass a high threshold on 1-2 lucky guesses.

This module computes a DERIVED mastery estimate only. Per the standing
architecture boundary already enforced elsewhere in this package (and
reinforced by the local RE governance memo at
`Distilled Analysis Reports/Reports/RE_PHASE1_USABLE_MECHANISMS_AND_GO_NO_GO_20260911.md`,
"a derived index, ledger, score, or schedule [must never be] treated as
reviewed truth"): `compute_mastery` NEVER writes to the database and NEVER
by itself confirms a mastery_decision. Callers pass its output to
`evidence.propose_evaluation` (already PENDING_CONFIRMATION-only) exactly
like any other evidence-derived proposal.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

GATE_THRESHOLD = 0.85  # Tutor MCP's unified BKT/KST gate.

# DeepTutor's recency weights for the last-N observed outcomes (oldest to
# newest); index 0 unused so weights[k] applies to the k-th most recent
# attempt when there are k total attempts considered.
_RECENCY_WEIGHTS = (0.5, 0.7, 0.85, 0.95, 1.0)

# DeepTutor's small-sample confidence cap: even a perfect run on too few
# attempts cannot report a high mastery estimate -- guards against passing
# the gate on 1-2 lucky guesses.
_CONFIDENCE_CAP = {1: 0.5, 2: 0.8}

# Standard BKT priors (Corbett & Anderson defaults commonly cited; Tutor
# MCP's report does not publish its exact prior constants, so these are
# the well-known textbook defaults, not a guess at Tutor MCP's private
# tuning -- documented here as such rather than presented as identical).
DEFAULT_P_INIT = 0.2  # prior P(mastery) before any evidence
DEFAULT_P_LEARN = 0.3  # P(transition unmastered -> mastered per attempt)
DEFAULT_P_SLIP = 0.1  # P(incorrect | mastered)
DEFAULT_P_GUESS = 0.2  # P(correct | unmastered)


@dataclass(frozen=True)
class MasteryEstimate:
    objective_version_id: str
    p_mastery: float  # BKT posterior, 0..1
    attempt_count: int
    recency_weighted_accuracy: float | None  # None if zero attempts (UNKNOWN)
    confidence_capped_estimate: float | None  # recency-weighted accuracy after the small-sample cap
    gate_passed: bool  # p_mastery >= GATE_THRESHOLD
    reason: str
    error_count: int = 0
    unresolved_misconceptions: tuple[str, ...] = ()

    def to_dict(self) -> dict:
        return {
            "objective_version_id": self.objective_version_id,
            "p_mastery": round(self.p_mastery, 4),
            "attempt_count": self.attempt_count,
            "recency_weighted_accuracy": (
                round(self.recency_weighted_accuracy, 4)
                if self.recency_weighted_accuracy is not None
                else None
            ),
            "confidence_capped_estimate": (
                round(self.confidence_capped_estimate, 4)
                if self.confidence_capped_estimate is not None
                else None
            ),
            "gate_passed": self.gate_passed,
            "gate_threshold": GATE_THRESHOLD,
            "reason": self.reason,
            "error_count": self.error_count,
            "unresolved_misconceptions": list(self.unresolved_misconceptions),
        }


def _bkt_update(p_mastery: float, correct: bool, *, p_learn: float, p_slip: float, p_guess: float) -> float:
    """One standard BKT posterior update, then apply the per-attempt
    learning transition. Never invented data -- ``correct`` must come
    from a real, explicit attempt outcome, never inferred.
    """
    if correct:
        numerator = p_mastery * (1 - p_slip)
        denominator = numerator + (1 - p_mastery) * p_guess
    else:
        numerator = p_mastery * p_slip
        denominator = numerator + (1 - p_mastery) * (1 - p_guess)
    posterior = numerator / denominator if denominator > 0 else p_mastery
    # Learning transition: even if still unmastered, some chance of moving
    # to mastered before the next observation.
    return posterior + (1 - posterior) * p_learn


def compute_mastery(
    db: sqlite3.Connection,
    objective_version_id: str,
    *,
    p_init: float = DEFAULT_P_INIT,
    p_learn: float = DEFAULT_P_LEARN,
    p_slip: float = DEFAULT_P_SLIP,
    p_guess: float = DEFAULT_P_GUESS,
) -> MasteryEstimate:
    """Compute a DERIVED BKT mastery estimate from real attempt/assessment
    evidence only. Correctness per attempt is read from the assessment's
    stored ``confidence_after`` (>=2 == "moderate"/"high" counts as a
    correct/understood observation, matching this package's own
    CONFIDENCE_ORDINAL scale) combined with whether any error was recorded
    -- never inferred from unrelated activity.
    """
    rows = db.execute(
        """
        SELECT a.criterion_scores_json, a.evidence_refs_json
          FROM assessment a
          JOIN attempt at ON at.id = a.attempt_id
          JOIN exercise_version ev ON ev.id = at.exercise_version_id
          JOIN exercise_objective eo ON eo.exercise_version_id = ev.id
         WHERE eo.objective_version_id = ?
           AND a.supersedes_id IS NULL
         ORDER BY at.submitted_at ASC, at.rowid ASC
        """,
        (objective_version_id,),
    ).fetchall()

    import json

    outcomes: list[bool] = []
    for row in rows:
        scores = json.loads(row["criterion_scores_json"])
        confidence_int = scores.get("confidence_int")
        evidence = json.loads(row["evidence_refs_json"]) if row["evidence_refs_json"] else {}
        has_errors = bool(evidence.get("errors"))
        if confidence_int is not None:
            correct = confidence_int >= 2 and not has_errors
        else:
            # Legacy/seed rows (label-only confidence, no explicit int):
            label = scores.get("confidence")
            correct = label in {"high", "moderate"} and not has_errors
        outcomes.append(correct)

    # Check error ledger for recorded misconceptions/errors
    errors_rows = []
    try:
        errors_rows = db.execute(
            """
            SELECT id, error_category, misconception, correction
              FROM error_ledger
             WHERE objective_version_id = ?
             ORDER BY recorded_at DESC, id DESC
            """,
            (objective_version_id,),
        ).fetchall()
    except sqlite3.OperationalError:
        errors_rows = []

    error_count = len(errors_rows)
    unresolved = tuple(
        str(row["misconception"])
        for row in errors_rows
        if not row["correction"]
    )

    if not outcomes:
        return MasteryEstimate(
            objective_version_id=objective_version_id,
            p_mastery=p_init,
            attempt_count=0,
            recency_weighted_accuracy=None,
            confidence_capped_estimate=None,
            gate_passed=False,
            reason="UNKNOWN: no attempts recorded yet",
            error_count=error_count,
            unresolved_misconceptions=unresolved,
        )

    p_mastery = p_init
    for correct in outcomes:
        p_mastery = _bkt_update(p_mastery, correct, p_learn=p_learn, p_slip=p_slip, p_guess=p_guess)

    n = len(outcomes)
    recent = outcomes[-len(_RECENCY_WEIGHTS) :]
    weights = _RECENCY_WEIGHTS[-len(recent) :]
    weighted_accuracy = sum(w * (1.0 if c else 0.0) for w, c in zip(weights, recent)) / sum(weights)

    cap = _CONFIDENCE_CAP.get(n)
    capped = min(weighted_accuracy, cap) if cap is not None else weighted_accuracy

    # The gate uses the more conservative of the two independent estimates
    # (BKT posterior vs. the confidence-capped recency-weighted accuracy)
    # so neither a lucky BKT sequence nor a lucky small sample alone can
    # pass the gate.
    effective = min(p_mastery, capped)
    gate_passed = effective >= GATE_THRESHOLD

    reason = (
        f"{n} attempt(s); BKT p_mastery={p_mastery:.3f}, "
        f"recency-weighted accuracy={weighted_accuracy:.3f}"
        + (f" capped to {capped:.3f} (n={n})" if cap is not None else "")
        + (f"; {error_count} error(s) in ledger ({len(unresolved)} unresolved)" if error_count else "")
        + f"; gate {'PASSED' if gate_passed else 'NOT MET'} at >= {GATE_THRESHOLD}"
    )

    return MasteryEstimate(
        objective_version_id=objective_version_id,
        p_mastery=p_mastery,
        attempt_count=n,
        recency_weighted_accuracy=weighted_accuracy,
        confidence_capped_estimate=capped,
        gate_passed=gate_passed,
        reason=reason,
        error_count=error_count,
        unresolved_misconceptions=unresolved,
    )
