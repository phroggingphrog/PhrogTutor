"""Typed shapes returned by the academia_learning API.

These mirror the JSON shapes specified for Academia's RAG response and
attempt-record contracts. Kept as frozen dataclasses with an explicit
``to_dict`` rather than free-form dicts so a schema change is a visible
diff, not a silent key rename.
"""

from __future__ import annotations

from dataclasses import dataclass, field

CONFIRMATION_ACTORS = frozenset(
    {
        "user-provided",
        "user-provided 2026-09-10",
        "deterministic-check",
        "instructor-feedback",
    }
)
CONFIRMATION_ACTOR_PLACEHOLDERS = ",".join("?" for _ in CONFIRMATION_ACTORS)


@dataclass(frozen=True)
class Citation:
    namespace: str
    course_id: str
    source_id: str
    source_version: str
    record_ref: str
    content_hash: str
    citation_locator: str
    byte_start: int
    byte_end: int
    exact_text: str
    evidence_class: str
    freshness_state: str  # FRESH | SUPERSEDED

    def to_dict(self) -> dict:
        return {
            "namespace": self.namespace,
            "course_id": self.course_id,
            "source_id": self.source_id,
            "source_version": self.source_version,
            "record_ref": self.record_ref,
            "content_hash": self.content_hash,
            "citation_locator": self.citation_locator,
            "byte_start": self.byte_start,
            "byte_end": self.byte_end,
            "exact_text": self.exact_text,
            "evidence_class": self.evidence_class,
            "freshness_state": self.freshness_state,
        }


@dataclass(frozen=True)
class LearningBlock:
    course_id: str
    objective_version_id: str
    concept: str
    definition: str
    selected_reason: str  # DEADLINE | PREREQUISITE | CONFIDENCE | SPACED_RETRIEVAL
    reason_detail: str
    prerequisite_ids: tuple[str, ...]
    unmet_prerequisites: tuple[str, ...]
    confidence_before: int | None  # 0-3, None = UNKNOWN (no demonstrated evidence yet)
    citations: tuple[Citation, ...]
    exercise_version_id: str | None
    minutes: int = 25
    # Read-only reflection of the learner's already-captured profile (see
    # learner_profile.py). Never invented, never adjusted per-block --
    # the same constraints/adaptations apply whenever a profile exists.
    # Empty tuples, never None, when no profile has been seeded.
    course_constraints: tuple[str, ...] = ()
    allowed_adaptations: tuple[str, ...] = ()

    def to_dict(self) -> dict:
        return {
            "course_id": self.course_id,
            "objective_version_id": self.objective_version_id,
            "concept": self.concept,
            "definition": self.definition,
            "selected_reason": self.selected_reason,
            "reason_detail": self.reason_detail,
            "prerequisite_ids": list(self.prerequisite_ids),
            "unmet_prerequisites": list(self.unmet_prerequisites),
            "confidence_before": self.confidence_before,
            "citations": [c.to_dict() for c in self.citations],
            "exercise_version_id": self.exercise_version_id,
            "minutes": self.minutes,
            "course_constraints": list(self.course_constraints),
            "allowed_adaptations": list(self.allowed_adaptations),
        }


@dataclass(frozen=True)
class AttemptReceipt:
    attempt_id: str
    exercise_version_id: str
    assessment_id: str
    evaluation_id: str
    audit_event_id: int
    objective_version_id: str
    confidence_after: int
    status: str  # PENDING_CONFIRMATION until a human/deterministic actor confirms

    def to_dict(self) -> dict:
        return {
            "attempt_id": self.attempt_id,
            "exercise_version_id": self.exercise_version_id,
            "assessment_id": self.assessment_id,
            "evaluation_id": self.evaluation_id,
            "audit_event_id": self.audit_event_id,
            "objective_version_id": self.objective_version_id,
            "confidence_after": self.confidence_after,
            "status": self.status,
        }


CONFIDENCE_LABELS = ("low", "moderate", "high")
# Ordinal encoding for demonstrated-confidence labels found in EXISTING
# assessment evidence, used only for stage-3 ranking (selection.py). An
# objective with NO assessment at all is represented as Python ``None``,
# never as an ordinal, and is never conflated with "low" or with a
# fabricated zero score -- see confidence.py. This is deliberately a
# separate concept from the explicit, learner-supplied 0-3
# confidence_before/confidence_after integers recorded on a fresh attempt
# (AttemptReceipt / evidence.append_attempt), which are raw human input,
# not a derived ranking label.
CONFIDENCE_ORDINAL = {"low": 1, "moderate": 2, "high": 3}
