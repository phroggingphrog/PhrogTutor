"""Read the existing, already-captured learner profile -- preferences,
course-specific notation constraints, and self-reported accessibility
context -- instead of inventing or re-collecting any of it.

This is genuinely "self data collection": a one-time, explicit,
user-provided profile seeded by ``agent/native_learning.py``'s
``seed_acha_profile()`` into ``audit_event`` (namespace='learning',
event_type='LEARNER_PROFILE_SEEDED', target_ref='learner:acha:profile').
This module only ever READS that row -- it never writes, never infers a
new preference, and never edits ``accessibility_context`` or
``mastery_effect``. Academia must not silently re-derive or override any
of this from conversation; if a preference has genuinely changed, that is
a new explicit, human-provided update to the same seeding mechanism, not
a job for this package.

Critical epistemic-safety property, preserved verbatim from the source
data: every accessibility_context entry the profile carries already
states its own ``mastery_effect`` (in the live profile: ``"none"`` for
the recorded ADHD context). Nothing in this package may let
accessibility_context influence `mastery.compute_mastery`'s arithmetic --
it may only ever affect PRESENTATION (pacing, chunk size, hint timing),
never the mastery estimate itself. See ``get_learner_profile``'s
docstring for the exact fields this guards.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass


@dataclass(frozen=True)
class LearnerProfile:
    name: str
    preferences: tuple[str, ...]
    course_constraints: tuple[str, ...]
    accessibility_context: tuple[dict, ...]
    support_references: tuple[dict, ...]
    provenance: str
    seeded_at: str

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "preferences": list(self.preferences),
            "course_constraints": list(self.course_constraints),
            "accessibility_context": list(self.accessibility_context),
            "support_references": list(self.support_references),
            "provenance": self.provenance,
            "seeded_at": self.seeded_at,
        }

    def allowed_adaptations(self) -> tuple[str, ...]:
        """Union of every accessibility_context entry's allowed
        adaptations. These bound PRESENTATION only (pacing, chunk size,
        hint timing, structure) -- never a mastery-scoring input.
        """
        out: list[str] = []
        for ctx in self.accessibility_context:
            for adaptation in ctx.get("allowed_adaptations", []):
                if adaptation not in out:
                    out.append(adaptation)
        return tuple(out)

    def mastery_effects_are_all_none(self) -> bool:
        """True only if every recorded accessibility_context explicitly
        declares no mastery effect. Callers that would let accessibility
        context touch a mastery computation MUST check this first and
        refuse if it is False (i.e. if the profile itself ever records a
        real mastery effect, that is a signal for a human decision, not
        something this package silently applies).
        """
        return all(ctx.get("mastery_effect") == "none" for ctx in self.accessibility_context)


def get_learner_profile(db: sqlite3.Connection, learner_ref: str = "learner:acha:profile") -> LearnerProfile | None:
    """Read-only. Returns None (not a fabricated default) if no profile
    has ever been seeded -- callers must disclose that, not invent one.
    """
    row = db.execute(
        "SELECT details_json, actor, occurred_at FROM audit_event "
        "WHERE namespace='learning' AND event_type='LEARNER_PROFILE_SEEDED' AND target_ref=? "
        "ORDER BY id DESC LIMIT 1",
        (learner_ref,),
    ).fetchone()
    if row is None:
        return None
    details = json.loads(row["details_json"])
    return LearnerProfile(
        name=details.get("name", "unknown"),
        preferences=tuple(details.get("preferences", [])),
        course_constraints=tuple(details.get("course_constraints", [])),
        accessibility_context=tuple(details.get("accessibility_context", [])),
        support_references=tuple(details.get("support_references", [])),
        provenance=details.get("provenance", row["actor"]),
        seeded_at=row["occurred_at"],
    )
