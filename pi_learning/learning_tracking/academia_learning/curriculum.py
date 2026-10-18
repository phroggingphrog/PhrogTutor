"""Top-down project learning loop and curriculum generator.

Implements the top-down systems-engineering learning model:
Whole system
  ↓
Subsystem
  ↓
Concept
  ↓
Incident or real change
  ↓
Small exercise
  ↓
Verification
  ↓
Review and mastery update

Turns live engineering observations into structured learning events with verified exercises.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional, Sequence

from .learning_event import LearningEvent, SourceSensitivity
from .path_allowlist import validate_evidence_path


@dataclass(frozen=True)
class TopDownCurriculumStep:
    system: str
    subsystem: str
    concept: str
    concept_tags: tuple[str, ...]
    observed_fact: str
    explanation: str
    exercise_task: str
    verification_command: str


def generate_learning_event_from_incident(
    event_id: str,
    system: str,
    subsystem: str,
    concept: str,
    concept_tags: Sequence[str],
    observed_fact: str,
    evidence_source: str,
    evidence_reference: str,
    explanation: str,
    exercise_task: str,
    verification_command: str,
    evidence_file_path: Optional[Path | str] = None,
    sensitivity: str = SourceSensitivity.OPERATIONAL.value,
    safe_roots: Optional[Sequence[Path]] = None,
    forbidden_roots: Optional[Sequence[Path]] = None,
    incident_or_change_ref: Optional[str] = None,
    hypothesis: Optional[str] = None,
    model_explanation: Optional[str] = None,
    verified_result: Optional[str] = None,
    sanitized_case_summary: bool = False,
) -> LearningEvent:
    """Construct a typed LearningEvent from an observed system incident or change.

    Validates evidence filesystem paths against the safe project allowlist.
    Separates top-down structural context (system -> subsystem -> concept) from
    the observed fact and exercise task.
    """
    evidence_items = [{"source": evidence_source, "reference": evidence_reference}]
    if evidence_file_path is not None:
        validated_path = validate_evidence_path(evidence_file_path, safe_roots, forbidden_roots)
        evidence_items.append({"source": "filesystem", "reference": str(validated_path)})

    # Top-down hierarchical explanation
    hierarchical_explanation = f"[{system} -> {subsystem} -> {concept}]: {explanation.strip()}"

    tags = list(dict.fromkeys([
        system.lower().replace(" ", "-"),
        subsystem.lower().replace(" ", "-"),
        *concept_tags,
    ]))

    return LearningEvent(
        id=event_id,
        source=evidence_source,
        sensitivity=sensitivity,
        concept_tags=tuple(tags),
        observed_fact=observed_fact.strip(),
        evidence=tuple(evidence_items),
        lesson={
            "concept": concept.strip(),
            "explanation": hierarchical_explanation,
        },
        exercise={
            "task": exercise_task.strip(),
        },
        verification={
            "command": verification_command.strip(),
        },
        whole_system=system.strip(),
        subsystem=subsystem.strip(),
        concept=concept.strip(),
        incident_or_change_ref=incident_or_change_ref,
        hypothesis=hypothesis,
        model_explanation=(model_explanation.strip() if model_explanation is not None else explanation.strip()),
        verified_result=verified_result,
        mastery="pending",
        sanitized_case_summary=sanitized_case_summary,
    )
