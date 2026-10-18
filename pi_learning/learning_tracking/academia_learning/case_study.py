"""Automatic case-study generation from verified systems-engineering incidents.

Transforms verified learning events and incident records from the
``learning_event`` ledger into bounded, sanitized practical case studies /
learning modules following top-down systems engineering principles:
Whole System -> Subsystem -> Concept -> Incident -> Exercise -> Verification.

Strips all sensitive identifiers, tokens, and paths.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import re
import sqlite3
from typing import Any, Dict, List, Optional, Sequence

from .learning_event import (
    LearningEvent,
    get_learning_event,
    list_learning_events,
)

_EMAIL_PATTERN = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b")
_SECRET_PATTERN = re.compile(
    r"(Bearer\s+[A-Za-z0-9._~+/-]+|ghp_[A-Za-z0-9]+|sk-[A-Za-z0-9]+|token=[^&\s]+|password=[^&\s]+|key=[^&\s]+|secret=[^&\s]+)",
    re.IGNORECASE,
)
_PRIVATE_PATH_PATTERN = re.compile(
    r"(/home/[^/\s]+/(\.ssh|\.gnupg|\.hermes/credentials|\.aws|\.config/gcloud|\.vault)[^/\s]*)",
    re.IGNORECASE,
)
_IP_PATTERN = re.compile(r"\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b")


def sanitize_case_study_text(text: str) -> str:
    """Strip sensitive tokens, credentials, emails, and private paths from text."""
    if not text:
        return ""
    s = _EMAIL_PATTERN.sub("[REDACTED_EMAIL]", text)
    s = _SECRET_PATTERN.sub("[REDACTED_SECRET]", s)
    s = _PRIVATE_PATH_PATTERN.sub("[REDACTED_PATH]", s)
    s = _IP_PATTERN.sub("[REDACTED_IP]", s)
    return s.strip()

class UnverifiedEventError(ValueError):
    """Raised when attempting to generate a case study from an unverified learning event."""



@dataclass(frozen=True)
class CaseStudyModule:
    id: str
    event_id: str
    title: str
    whole_system: str
    subsystem: str
    concept: str
    concept_tags: tuple[str, ...]
    observed_incident: str
    explanation: str
    exercise_task: str
    verification_command: str
    expected_result: str
    epistemic_provenance: Dict[str, Any]
    sanitized: bool

    def to_dict(self) -> Dict[str, Any]:
        return {
            "case_study_id": self.id,
            "event_id": self.event_id,
            "title": self.title,
            "whole_system": self.whole_system,
            "subsystem": self.subsystem,
            "concept": self.concept,
            "concept_tags": list(self.concept_tags),
            "observed_incident": self.observed_incident,
            "explanation": self.explanation,
            "exercise_task": self.exercise_task,
            "verification_command": self.verification_command,
            "expected_result": self.expected_result,
            "epistemic_provenance": self.epistemic_provenance,
            "sanitized": self.sanitized,
        }

    def to_markdown(self) -> str:
        tags_str = ", ".join(f"`{t}`" for t in self.concept_tags)
        return (
            f"# Case Study: {self.title}\n\n"
            f"**Case Study ID**: `{self.id}` (Event: `{self.event_id}`)\n"
            f"**Tags**: {tags_str}\n"
            f"**Sanitized**: {'Yes' if self.sanitized else 'No'}\n\n"
            f"## 1. System Architecture Context\n"
            f"- **System**: {self.whole_system}\n"
            f"- **Subsystem**: {self.subsystem}\n"
            f"- **Core Concept**: {self.concept}\n\n"
            f"## 2. Theoretical Background & Invariants\n"
            f"{self.explanation}\n\n"
            f"## 3. Observed Incident / Real Failure Mode\n"
            f"{self.observed_incident}\n\n"
            f"## 4. Hands-On Exercise Task\n"
            f"{self.exercise_task}\n\n"
            f"## 5. Verification & Acceptance Criteria\n"
            f"Run the following verification command:\n"
            f"```bash\n{self.verification_command}\n```\n\n"
            f"**Expected Outcome**:\n"
            f"```\n{self.expected_result}\n```\n\n"
            f"## Epistemic Provenance\n"
            f"- Source: `{self.epistemic_provenance.get('source')}`\n"
            f"- Sensitivity: `{self.epistemic_provenance.get('sensitivity')}`\n"
            f"- Verified Result: `{self.epistemic_provenance.get('verified_result')}`\n"
        )


def generate_case_study_from_event(
    event: LearningEvent,
    *,
    require_verified: bool = True,
) -> CaseStudyModule:
    """Generate a sanitized, bounded systems engineering CaseStudyModule from a LearningEvent.

    Fails closed: requires a verified learning incident with non-empty verified_result.
    Dummy or fabricated verification outputs are prohibited.
    """
    if require_verified and (not event.verified_result or not str(event.verified_result).strip()):
        raise UnverifiedEventError(
            f"LearningEvent '{event.id}' is unverified (missing verified_result). "
            "Case studies must only be generated from verified incidents."
        )
    if not event.verified_result or not str(event.verified_result).strip():
        raise UnverifiedEventError(
            f"LearningEvent '{event.id}' has no verified_result."
        )

    cs_id = f"cs-{event.id}"
    title = f"{event.whole_system.title()} / {event.subsystem.title()} - {event.concept}"

    raw_incident = (
        event.observed_fact
        + (f"\n\nHypothesis: {event.hypothesis}" if event.hypothesis else "")
        + (f"\n\nModel Analysis: {event.model_explanation}" if event.model_explanation else "")
    )
    sanitized_incident = sanitize_case_study_text(raw_incident)

    raw_explanation = event.lesson.get("explanation", "")
    sanitized_explanation = sanitize_case_study_text(raw_explanation)

    raw_exercise = event.exercise.get("task", "")
    sanitized_exercise = sanitize_case_study_text(raw_exercise)

    raw_verification = event.verification.get("command", "")
    sanitized_verification = sanitize_case_study_text(raw_verification)

    raw_expected = event.verified_result
    sanitized_expected = sanitize_case_study_text(raw_expected)
    clean_tags = tuple(sanitize_case_study_text(t) for t in event.concept_tags)

    provenance = {
        "source": sanitize_case_study_text(event.source),
        "sensitivity": event.sensitivity,
        "incident_or_change_ref": sanitize_case_study_text(event.incident_or_change_ref or ""),
        "verified_result": sanitize_case_study_text(event.verified_result or ""),
        "mastery_state": event.mastery,
    }

    return CaseStudyModule(
        id=cs_id,
        event_id=event.id,
        title=sanitize_case_study_text(title),
        whole_system=sanitize_case_study_text(event.whole_system),
        subsystem=sanitize_case_study_text(event.subsystem),
        concept=sanitize_case_study_text(event.concept),
        concept_tags=clean_tags,
        observed_incident=sanitized_incident,
        explanation=sanitized_explanation,
        exercise_task=sanitized_exercise,
        verification_command=sanitized_verification,
        expected_result=sanitized_expected,
        epistemic_provenance=provenance,
        sanitized=True,
    )


def generate_case_studies(
    db: sqlite3.Connection,
    *,
    event_id: Optional[str] = None,
    source: Optional[str] = None,
    require_verified: bool = True,
) -> List[CaseStudyModule]:
    """Generate sanitized case studies from verified learning events.

    Defaults to require_verified=True and fails closed.
    """
    try:
        if event_id:
            ev = get_learning_event(db, event_id)
            if ev is None:
                return []
            if require_verified and (not ev.verified_result or not str(ev.verified_result).strip()):
                raise UnverifiedEventError(
                    f"LearningEvent '{event_id}' is unverified (missing verified_result)."
                )
            return [generate_case_study_from_event(ev, require_verified=require_verified)]

        events = list_learning_events(db, source=source)
    except sqlite3.OperationalError:
        return []

    case_studies = []
    for ev in events:
        if require_verified and (not ev.verified_result or not str(ev.verified_result).strip()):
            continue
        try:
            case_studies.append(generate_case_study_from_event(ev, require_verified=require_verified))
        except UnverifiedEventError:
            continue
    return case_studies
