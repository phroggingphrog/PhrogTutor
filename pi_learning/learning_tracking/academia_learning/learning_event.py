"""Learning event ledger, source allowlist, and provenance model.

Implements the P0 top-down project learning loop for live systems-engineering
curriculum, turning real runtime events (Hermes, VPS, Canvas) into bounded lessons.
Events are stored append-only in learning.sqlite3's additive learning_event table.

Strictly separates epistemic classes:
- observed_fact
- model_explanation
- hypothesis
- exercise
- verified_result
Never turns model inference into canonical truth without verified results.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import json
from pathlib import Path
import re
import sqlite3
from typing import Any, Dict, List, Optional, Sequence, Tuple

DEFAULT_POLICY_PATH = Path(__file__).resolve().parents[2] / "policy" / "hermes-learning-integration-v1.json"  # pi-tutor: copied next to the code (the old data/hermes path no longer exists)

# Fallback default safe sources in case policy JSON is unreachable
DEFAULT_SAFE_SOURCES = frozenset({
    "hermes_runtime",
    "hermes_config",
    "git_diff",
    "cron_execution",
    "mcp_health",
    "sanitized_service_log",
    "test_execution",
    "verified_command",
    "canvas_formal",
    "approved_project_doc",
})

RESTRICTED_SOURCES = frozenset({
    "email_content",
    "apple_id",
    "personal_calendar",
    "private_document",
    "credential_store",
    "raw_auth_log",
    "unrestricted_filesystem",
})

_SPECULATIVE_HEDGES = re.compile(
    r"\b(maybe|perhaps|probably|i think|i assume|presumably|supposedly)\b",
    re.IGNORECASE,
)

VALID_MASTERY_STATES = frozenset({"pending", "solid", "weak", "reviewed"})


class SourceSensitivity(str, Enum):
    OPERATIONAL = "operational"
    ACADEMIC = "academic"
    RESTRICTED = "restricted"


class EpistemicClass(str, Enum):
    OBSERVED_FACT = "observed_fact"
    MODEL_EXPLANATION = "model_explanation"
    HYPOTHESIS = "hypothesis"
    EXERCISE = "exercise"
    VERIFIED_RESULT = "verified_result"


def load_allowed_sources(policy_path: Optional[Path] = None, *, fail_closed: bool = True) -> frozenset[str]:
    """Load allowed automatic learning sources from project learning policy.

    Fails closed if the policy file is missing or contains malformed JSON.
    """
    p = policy_path or DEFAULT_POLICY_PATH
    if not p.exists():
        if fail_closed:
            raise FileNotFoundError(f"Learning policy file not found: {p}")
        return DEFAULT_SAFE_SOURCES
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception as e:
        if fail_closed:
            raise ValueError(f"Malformed learning policy at {p}: {e}") from e
        return DEFAULT_SAFE_SOURCES

    if data.get("exclusive") or data.get("override_default_sources"):
        sources = set()
    else:
        sources = set(DEFAULT_SAFE_SOURCES)
    # Support explicit source-policy fields while retaining compatibility
    # with the existing curriculum map used by the live policy.
    for key in ("allowed_sources", "allowed_source_kinds", "safe_automatic_sources"):
        values = data.get(key, ())
        if not isinstance(values, (list, tuple, set)):
            raise ValueError(f"Learning policy field {key!r} must be a list")
        sources.update(str(value).strip() for value in values if str(value).strip())
    source_policy = data.get("source_policy", {})
    if source_policy:
        if not isinstance(source_policy, dict):
            raise ValueError("Learning policy field 'source_policy' must be an object")
        values = source_policy.get(
            "allowed_sources",
            source_policy.get("allowed_source_kinds", ()),
        )
        if not isinstance(values, (list, tuple, set)):
            raise ValueError("Learning policy source_policy allowed sources must be a list")
        sources.update(str(value).strip() for value in values if str(value).strip())
    for key in ("disallowed_sources", "blocked_sources", "forbidden_sources"):
        values = data.get(key, ())
        if not isinstance(values, (list, tuple, set)):
            raise ValueError(f"Learning policy field {key!r} must be a list")
        sources.difference_update(str(value).strip() for value in values if str(value).strip())
    cmap = data.get("course_to_hermes_map", {})
    if not isinstance(cmap, dict):
        raise ValueError("Learning policy field 'course_to_hermes_map' must be an object")
    for course_info in cmap.values():
        if not isinstance(course_info, dict):
            raise ValueError("Learning policy course mappings must be objects")
        for layer in course_info.get("hermes_layers", []):
            sources.add(f"hermes_{layer}")
    return frozenset(sources)

@dataclass(frozen=True)
class LearningEvent:
    id: str
    source: str
    sensitivity: str
    concept_tags: tuple[str, ...]
    observed_fact: str
    evidence: tuple[dict[str, str], ...]
    lesson: dict[str, str]
    exercise: dict[str, str]
    verification: dict[str, str]
    # Hierarchy is required for every newly-created event.  Legacy database
    # rows are normalized explicitly by the database hydration helpers.
    whole_system: str
    subsystem: str
    concept: str
    incident_or_change_ref: Optional[str] = None
    hypothesis: Optional[str] = None
    model_explanation: Optional[str] = None
    verified_result: Optional[str] = None
    mastery: str = "pending"
    sanitized_case_summary: bool = False
    created_at: Optional[str] = None

    def __post_init__(self) -> None:
        """Enforce structural invariants only.

        Source authorization and filesystem-root membership depend on the
        caller's policy/context and therefore belong in
        ``validate_learning_event_payload``.
        """
        required_strings = {
            "learning_event id": self.id,
            "learning_event source": self.source,
            "whole_system": self.whole_system,
            "subsystem": self.subsystem,
            "concept": self.concept,
            "observed_fact": self.observed_fact,
        }
        for name, value in required_strings.items():
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} is required")

        valid_sensitivities = {s.value for s in SourceSensitivity}
        if not isinstance(self.sensitivity, str) or self.sensitivity not in valid_sensitivities:
            raise ValueError(
                f"sensitivity must be one of {sorted(valid_sensitivities)}, "
                f"got {self.sensitivity!r}"
            )

        if not isinstance(self.concept_tags, (tuple, list)) or any(
            not isinstance(tag, str) for tag in self.concept_tags
        ):
            raise ValueError("concept_tags must be a tuple/list of strings")

        # Restricted-source handling is a structural privacy invariant; the
        # broader source allowlist remains policy-dependent.
        source_clean = self.source.strip()
        if source_clean in RESTRICTED_SOURCES:
            if self.sensitivity != SourceSensitivity.RESTRICTED.value:
                raise ValueError(
                    f"Restricted source {source_clean!r} cannot be tagged with "
                    f"sensitivity {self.sensitivity!r}; must be "
                    f"{SourceSensitivity.RESTRICTED.value!r}."
                )
            if self.sanitized_case_summary is not True:
                raise ValueError(
                    f"Source {source_clean!r} is a restricted source and cannot "
                    "be ingested without explicit sanitized_case_summary=True."
                )
        elif self.sensitivity == SourceSensitivity.RESTRICTED.value:
            raise ValueError(
                f"Safe source {source_clean!r} cannot be tagged with "
                "sensitivity 'restricted'."
            )

        if not isinstance(self.sanitized_case_summary, bool):
            raise ValueError("sanitized_case_summary must be a bool")

        if _SPECULATIVE_HEDGES.search(self.observed_fact):
            raise ValueError(
                "observed_fact must contain strictly observed factual "
                f"statements, found speculative wording: {self.observed_fact!r}"
            )

        if not isinstance(self.evidence, (tuple, list)) or not self.evidence:
            raise ValueError("learning_event must carry at least one evidence item")
        for ev in self.evidence:
            if not isinstance(ev, dict) or not isinstance(ev.get("source"), str):
                raise ValueError("each evidence item must have a string 'source' value")
            if not ev["source"].strip():
                raise ValueError("each evidence item source must not be empty")
            if not isinstance(ev.get("reference"), str):
                raise ValueError("each evidence item must have a string 'reference' value")
            if not ev["reference"].strip():
                if ev["source"] == "filesystem":
                    raise ValueError("filesystem evidence reference path must not be empty")
                raise ValueError("each evidence item reference must not be empty")

        for name, value, required in (
            ("lesson", self.lesson, ("concept", "explanation")),
            ("exercise", self.exercise, ("task",)),
            ("verification", self.verification, ("command",)),
        ):
            if not isinstance(value, dict) or any(
                not isinstance(item, str) for item in value.values()
            ):
                raise ValueError(f"{name} must be a dict with string values")
            if any(key not in value for key in required):
                raise ValueError(f"{name} must contain {required!r} fields")
            if any(not value[key].strip() for key in required):
                raise ValueError(f"{name} required values must be non-empty")

        for name in (
            "incident_or_change_ref",
            "hypothesis",
            "model_explanation",
            "verified_result",
            "created_at",
        ):
            value = getattr(self, name)
            if value is not None and (not isinstance(value, str) or not value.strip()):
                raise ValueError(f"{name} must be non-empty when provided")

        if not isinstance(self.mastery, str) or self.mastery not in VALID_MASTERY_STATES:
            raise ValueError(
                f"mastery must be one of {sorted(VALID_MASTERY_STATES)}, "
                f"got {self.mastery!r}"
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "learning_event": {
                "id": self.id,
                "source": self.source,
                "sensitivity": self.sensitivity,
                "whole_system": self.whole_system,
                "subsystem": self.subsystem,
                "concept": self.concept,
                "incident_or_change_ref": self.incident_or_change_ref,
                "concept_tags": list(self.concept_tags),
                "observed_fact": self.observed_fact,
                "hypothesis": self.hypothesis,
                "model_explanation": self.model_explanation,
                "evidence": list(self.evidence),
                "lesson": dict(self.lesson),
                "exercise": dict(self.exercise),
                "verified_result": self.verified_result,
                "verification": dict(self.verification),
                "mastery": self.mastery,
                "sanitized_case_summary": self.sanitized_case_summary,
                "created_at": self.created_at,
            }
        }

    def to_yaml(self) -> str:
        """Format strictly according to the learning event YAML contract."""
        lines = [
            "learning_event:",
            f"  id: {self.id}",
            f"  source: {self.source}",
            f"  sensitivity: {self.sensitivity}",
            f"  whole_system: {self.whole_system}",
            f"  subsystem: {self.subsystem}",
            f"  concept: {self.concept}",
            "  concept_tags:",
        ]
        for tag in self.concept_tags:
            lines.append(f"    - {tag}")
        if self.incident_or_change_ref is not None:
            lines.append(f"  incident_or_change_ref: {self.incident_or_change_ref}")
        escaped_hypothesis = (self.hypothesis or "").replace('"', '\\"')
        escaped_model = (self.model_explanation or "").replace('"', '\\"')
        if self.hypothesis is not None:
            lines.append(f'  hypothesis: "{escaped_hypothesis}"')
        if self.model_explanation is not None:
            lines.append(f'  model_explanation: "{escaped_model}"')
        escaped_fact = self.observed_fact.replace('"', '\\"')
        lines.append(f'  observed_fact: "{escaped_fact}"')
        lines.append("  evidence:")
        for ev in self.evidence:
            lines.append(f"    - source: {ev.get('source', '')}")
            lines.append(f"      reference: {ev.get('reference', '')}")
        lines.append("  lesson:")
        concept_esc = self.lesson.get("concept", "").replace('"', '\\"')
        explanation_esc = self.lesson.get("explanation", "").replace('"', '\\"')
        lines.append(f'    concept: "{concept_esc}"')
        lines.append(f'    explanation: "{explanation_esc}"')
        lines.append("  exercise:")
        task_esc = self.exercise.get("task", "").replace('"', '\\"')
        lines.append(f'    task: "{task_esc}"')
        lines.append("  verification:")
        cmd_esc = self.verification.get("command", "").replace('"', '\\"')
        lines.append(f'    command: "{cmd_esc}"')
        if self.verified_result is not None:
            escaped_verified = self.verified_result.replace('"', '\\"')
            lines.append(f'  verified_result: "{escaped_verified}"')
        lines.append(f"  mastery: {self.mastery}")
        lines.append(f"  sanitized_case_summary: {'true' if self.sanitized_case_summary else 'false'}")
        return "\n".join(lines) + "\n"

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> LearningEvent:
        """Build an event from an untrusted dictionary without coercion."""
        if not isinstance(data, dict):
            raise TypeError(f"Expected dict, got {type(data).__name__}")
        root = data.get("learning_event", data)
        if not isinstance(root, dict):
            raise ValueError("learning_event must be a mapping")

        def required_string(name: str) -> str:
            value = root.get(name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} must be a non-empty string")
            return value

        required = {
            name: required_string(name)
            for name in (
                "id",
                "source",
                "observed_fact",
                "whole_system",
                "subsystem",
                "concept",
            )
        }
        sensitivity = root.get("sensitivity", SourceSensitivity.OPERATIONAL.value)
        if not isinstance(sensitivity, str) or sensitivity not in {
            item.value for item in SourceSensitivity
        }:
            raise ValueError("sensitivity must be a valid string enum value")

        tags = root.get("concept_tags", ())
        if not isinstance(tags, (list, tuple)) or any(not isinstance(tag, str) for tag in tags):
            raise ValueError("concept_tags must be a list/tuple of strings")

        evidence = root.get("evidence", ())
        if not isinstance(evidence, (list, tuple)) or not evidence:
            raise ValueError("evidence must be a non-empty list/tuple of mappings")
        for item in evidence:
            if not isinstance(item, dict):
                raise ValueError("each evidence item must be a mapping")
            for key in ("source", "reference"):
                value = item.get(key)
                if not isinstance(value, str) or not value.strip():
                    raise ValueError(f"evidence {key} must be a non-empty string")

        nested: dict[str, dict[str, str]] = {}
        for name, keys in (
            ("lesson", ("concept", "explanation")),
            ("exercise", ("task",)),
            ("verification", ("command",)),
        ):
            value = root.get(name)
            if not isinstance(value, dict) or any(
                not isinstance(item, str) for item in value.values()
            ):
                raise ValueError(f"{name} must be a dict with string values")
            for key in keys:
                if key not in value or not value[key].strip():
                    raise ValueError(f"{name}.{key} must be a non-empty string")
            nested[name] = dict(value)

        optional: dict[str, Optional[str]] = {}
        for name in (
            "incident_or_change_ref",
            "hypothesis",
            "model_explanation",
            "verified_result",
            "created_at",
        ):
            value = root.get(name)
            if value is not None and not isinstance(value, str):
                raise ValueError(f"{name} must be a string when provided")
            optional[name] = value

        sanitized = root.get("sanitized_case_summary", False)
        if not isinstance(sanitized, bool):
            raise ValueError("sanitized_case_summary must be a bool")
        mastery = root.get("mastery", "pending")
        if not isinstance(mastery, str) or mastery not in VALID_MASTERY_STATES:
            raise ValueError("mastery must be a valid string enum value")

        return cls(
            **required,
            sensitivity=sensitivity,
            concept_tags=tuple(tags),
            evidence=tuple(dict(item) for item in evidence),
            lesson=nested["lesson"],
            exercise=nested["exercise"],
            verification=nested["verification"],
            **optional,
            mastery=mastery,
            sanitized_case_summary=sanitized,
        )


def insert_learning_event(conn: sqlite3.Connection, event: LearningEvent) -> None:
    """Store a learning event append-only in learning.sqlite3."""
    from datetime import datetime, timezone
    now_str = event.created_at or datetime.now(timezone.utc).isoformat()
    conn.execute(
        """
        INSERT INTO learning_event(
            id, source, sensitivity, whole_system, subsystem, concept,
            incident_or_change_ref, concept_tags_json, observed_fact,
            hypothesis, model_explanation, evidence_json, lesson_json,
            exercise_json, verified_result, verification_json, mastery,
            sanitized_case_summary, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            event.id,
            event.source,
            event.sensitivity,
            event.whole_system,
            event.subsystem,
            event.concept,
            event.incident_or_change_ref,
            json.dumps(list(event.concept_tags), ensure_ascii=False),
            event.observed_fact,
            event.hypothesis,
            event.model_explanation,
            json.dumps(list(event.evidence), ensure_ascii=False),
            json.dumps(dict(event.lesson), ensure_ascii=False),
            json.dumps(dict(event.exercise), ensure_ascii=False),
            event.verified_result,
            json.dumps(dict(event.verification), ensure_ascii=False),
            event.mastery,
            int(event.sanitized_case_summary),
            now_str,
        ),
    )


def _legacy_hierarchy(value: Any) -> str:
    """Normalize nullable hierarchy columns from pre-hierarchy rows."""
    if value is None or (isinstance(value, str) and not value.strip()):
        return "legacy"
    if not isinstance(value, str):
        raise ValueError("stored learning_event hierarchy must be a non-empty string")
    return value


def _db_bool(value: Any) -> bool:
    """Convert SQLite's 0/1 representation without accepting arbitrary truthiness."""
    if type(value) is bool:
        return value
    if type(value) is int and value in (0, 1):
        return bool(value)
    raise ValueError("stored sanitized_case_summary must be SQLite integer 0 or 1")


def _event_from_db_row(row: Any) -> LearningEvent:
    lesson = dict(json.loads(row[12]))
    return LearningEvent(
        id=row[0],
        source=row[1],
        sensitivity=row[2],
        whole_system=_legacy_hierarchy(row[3]),
        subsystem=_legacy_hierarchy(row[4]),
        concept=_legacy_hierarchy(row[5]),
        incident_or_change_ref=row[6],
        concept_tags=tuple(json.loads(row[7])),
        observed_fact=row[8],
        hypothesis=row[9],
        model_explanation=row[10],
        evidence=tuple(json.loads(row[11])),
        lesson=lesson,
        exercise=dict(json.loads(row[13])),
        verified_result=row[14],
        verification=dict(json.loads(row[15])),
        mastery=row[16],
        sanitized_case_summary=_db_bool(row[17]),
        created_at=row[18],
    )


def get_learning_event(conn: sqlite3.Connection, event_id: str) -> Optional[LearningEvent]:
    """Retrieve a single learning event by id from SQLite."""
    cursor = conn.execute(
        """
        SELECT id, source, sensitivity, whole_system, subsystem, concept,
               incident_or_change_ref, concept_tags_json, observed_fact,
               hypothesis, model_explanation, evidence_json, lesson_json,
               exercise_json, verified_result, verification_json, mastery,
               sanitized_case_summary, created_at
        FROM learning_event WHERE id = ?
        """,
        (event_id,),
    )
    row = cursor.fetchone()
    return _event_from_db_row(row) if row else None


def list_learning_events(
    conn: sqlite3.Connection,
    source: Optional[str] = None,
    mastery: Optional[str] = None,
) -> list[LearningEvent]:
    """List learning events from SQLite with optional filtering."""
    query = """
        SELECT id, source, sensitivity, whole_system, subsystem, concept,
               incident_or_change_ref, concept_tags_json, observed_fact,
               hypothesis, model_explanation, evidence_json, lesson_json,
               exercise_json, verified_result, verification_json, mastery,
               sanitized_case_summary, created_at
        FROM learning_event WHERE 1=1
    """
    params: list[Any] = []
    if source:
        query += " AND source = ?"
        params.append(source)
    if mastery:
        query += " AND mastery = ?"
        params.append(mastery)
    query += " ORDER BY created_at ASC"

    return [
        _event_from_db_row(row)
        for row in conn.execute(query, tuple(params)).fetchall()
    ]


def validate_learning_event_payload(
    payload: Any,
    *,
    policy_path: Optional[Path] = None,
    safe_roots: Optional[Sequence[Path]] = None,
) -> LearningEvent:
    """Validate a LearningEvent instance or raw dictionary against policy and allowlist.

    Fails closed:
    - Verifies policy exists and is valid JSON.
    - Ensures event source is allowed by that policy.
    - Ensures all filesystem evidence items are within safe_roots.
    - Ensures all epistemic invariants hold.
    """
    allowed_sources = load_allowed_sources(policy_path, fail_closed=True)
    if isinstance(payload, dict):
        event = LearningEvent.from_dict(payload)
    elif isinstance(payload, LearningEvent):
        event = payload
    else:
        raise TypeError(f"Expected LearningEvent or dict, got {type(payload).__name__}")

    source_clean = event.source.strip()
    if source_clean not in RESTRICTED_SOURCES and source_clean not in allowed_sources:
        raise ValueError(
            f"Unknown learning source {source_clean!r}: not authorized by "
            f"learning policy ({len(allowed_sources)} allowed sources)"
        )

    # Validate all filesystem references against the specified safe_roots
    from .path_allowlist import validate_evidence_path
    for ev in event.evidence:
        if ev.get("source") == "filesystem":
            ref = str(ev.get("reference", "")).strip()
            validate_evidence_path(ref, safe_roots=safe_roots)

    return event
