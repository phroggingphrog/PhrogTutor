"""Public academia_learning API boundary.

Only these functions are meant to be called from outside this package
(from Academia's own orchestration, or from the CLI in ``__main__.py``).
Nothing here exposes arbitrary SQL or unrestricted file access -- every
write is narrow and typed (matches the API boundary specified for
Academia: start_block / retrieve_evidence / append_attempt / read_progress
/ record_review / propose_evaluation / confirm_evaluation).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional, Sequence
from . import evidence as evidence_mod
from . import learner_profile as profile_mod
from . import mastery as mastery_mod
from . import selection as selection_mod
from . import spaced_retrieval as sr_mod
from .citations import CitationValidationError
from .db import DEFAULT_DB_PATH, connect, ensure_review_event_schema
from .grading import GradeResult, grade_answer
from .models import AttemptReceipt, LearningBlock
from .selection import NoCandidateError


class AcademiaLearningError(RuntimeError):
    pass


class InsufficientEvidenceError(AcademiaLearningError):
    """Raised (never silently papered over) when no citable, validated
    source evidence exists for the selected block."""


class ValidationFailedError(AcademiaLearningError):
    """A stored citation failed live re-validation (tampered/stale/missing)."""


def start_block(
    *,
    db_path: Path | None = None,
    canvas_summary_path: Path | None = None,
    now: str | None = None,
    require_citations: bool = False,
) -> dict:
    """Select exactly one 25-minute learning block with validated citations.

    Read-only: opens the store in ``mode=ro`` and never writes. Returns the
    block as a plain dict (JSON-serializable) matching the RAG response
    shape (course_id, concepts, citations, ...). ``require_citations=True``
    raises ``InsufficientEvidenceError`` instead of returning a block with
    zero citations -- callers that need grounded excerpts should set this;
    the default first-slice CLI leaves it False so a real prerequisite/
    confidence-only block (no source excerpt needed yet) is not blocked.
    """
    path = db_path or DEFAULT_DB_PATH
    db = connect(path, write=False)
    try:
        try:
            block: LearningBlock = selection_mod.select_next_block(
                db, canvas_summary_path=canvas_summary_path, now=now
            )
        except NoCandidateError as exc:
            raise InsufficientEvidenceError(str(exc)) from exc
        if require_citations and not block.citations:
            raise InsufficientEvidenceError(
                f"no validated source citation available for {block.objective_version_id}"
            )
        return block.to_dict()
    finally:
        db.close()


def retrieve_evidence(objective_version_id: str, *, db_path: Path | None = None) -> dict:
    """Cross-session readback: the most recent attempt/evaluation state
    for one objective, used to prove/implement resumability.
    """
    path = db_path or DEFAULT_DB_PATH
    db = connect(path, write=False)
    try:
        return evidence_mod.read_progress(db, objective_version_id)
    finally:
        db.close()


def append_attempt(
    *,
    objective_version_id: str,
    response_ref: str,
    assistance_level: str,
    confidence_before: int | None,
    confidence_after: int,
    checks: list[dict] | None = None,
    errors: list[str] | None = None,
    corrections: list[str] | None = None,
    source_refs: list[str] | None = None,
    next_action: str,
    exercise_version_id: str | None = None,
    supersedes_assessment_id: str | None = None,
    actor: str = "user-provided",
    db_path: Path | None = None,
) -> dict:
    """Append exactly one attempt/assessment/evaluation-proposal/audit
    event. Opens the store read-write for the duration of one transaction
    and commits or rolls back atomically.
    """
    path = db_path or DEFAULT_DB_PATH
    db = connect(path, write=True)
    try:
        db.execute("BEGIN IMMEDIATE")
        receipt: AttemptReceipt = evidence_mod.append_attempt(
            db,
            objective_version_id=objective_version_id,
            response_ref=response_ref,
            assistance_level=assistance_level,
            checks=checks or [],
            errors=errors or [],
            corrections=corrections or [],
            confidence_before=confidence_before,
            confidence_after=confidence_after,
            source_refs=source_refs or [],
            next_action=next_action,
            exercise_version_id=exercise_version_id,
            supersedes_assessment_id=supersedes_assessment_id,
            actor=actor,
        )
        db.commit()
        return receipt.to_dict()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def propose_evaluation(
    *,
    objective_version_id: str,
    assessment_id: str | None,
    proposed_state: str,
    evidence_refs: list[str],
    db_path: Path | None = None,
) -> str:
    path = db_path or DEFAULT_DB_PATH
    db = connect(path, write=True)
    try:
        db.execute("BEGIN IMMEDIATE")
        evaluation_id = evidence_mod.propose_evaluation(
            db,
            objective_version_id=objective_version_id,
            assessment_id=assessment_id,
            proposed_state=proposed_state,
            evidence_refs=evidence_refs,
        )
        db.commit()
        return evaluation_id
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def confirm_evaluation(
    *, evaluation_id: str, actor: str, decision: str, db_path: Path | None = None
) -> int:
    path = db_path or DEFAULT_DB_PATH
    db = connect(path, write=True)
    try:
        db.execute("BEGIN IMMEDIATE")
        decision_id = evidence_mod.confirm_evaluation(
            db, evaluation_id, actor=actor, decision=decision
        )
        db.commit()
        return decision_id
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def read_progress(objective_version_id: str, *, db_path: Path | None = None) -> dict:
    return retrieve_evidence(objective_version_id, db_path=db_path)


def record_review(
    *,
    objective_version_id: str,
    attempt_id: str | None,
    rating: str,
    verified_result: str,
    assistance_used: bool = False,
    evidence_refs: list[str] | None = None,
    db_path: Path | None = None,
) -> dict:
    path = db_path or DEFAULT_DB_PATH
    ensure_review_event_schema(path)
    db = connect(path, write=True)
    try:
        db.execute("BEGIN IMMEDIATE")
        result = sr_mod.record_review(
            db,
            objective_version_id=objective_version_id,
            attempt_id=attempt_id,
            rating=rating,
            verified_result=verified_result,
            assistance_used=assistance_used,
            evidence_refs=evidence_refs or [],
        )
        db.commit()
        return result
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def mastery_status(objective_version_id: str, *, db_path: Path | None = None) -> dict:
    """Derived BKT mastery estimate + gate status for one objective.

    Read-only. Never writes, never confirms anything -- purely a computed
    view over existing attempt/assessment evidence. Callers who want this
    reflected in learner state must separately call ``propose_evaluation``
    (and, later, an explicit ``confirm_evaluation``).
    """
    path = db_path or DEFAULT_DB_PATH
    db = connect(path, write=False)
    try:
        return mastery_mod.compute_mastery(db, objective_version_id).to_dict()
    finally:
        db.close()


def grade(response: str, expected: str, *, keywords: list[str] | None = None) -> dict:
    """Deterministic first-pass grading (see grading.py). Pure function,
    no database access -- exposed here so callers don't need to import
    the submodule directly.
    """
    result: GradeResult = grade_answer(response, expected, keywords=keywords)
    return result.to_dict()


def learner_profile(*, db_path: Path | None = None) -> dict | None:
    """The existing, already-captured learner profile (preferences,
    course_constraints, accessibility_context, support_references).
    Read-only. Returns None (not a fabricated default) if none has ever
    been seeded.
    """
    path = db_path or DEFAULT_DB_PATH
    db = connect(path, write=False)
    try:
        profile = profile_mod.get_learner_profile(db)
        return profile.to_dict() if profile else None
    finally:
        db.close()

def validate_learning_event(
    event: Any,
    *,
    policy_path: Path | None = None,
    safe_roots: Sequence[Path] | None = None,
) -> dict:
    """Dry-run validation of a LearningEvent instance or raw dict against schema, policy, and allowlist.

    Fails closed: returns the validated event dictionary without touching the database.
    Raises ValueError or PermissionError if any validation check fails.
    """
    from .learning_event import validate_learning_event_payload
    validated = validate_learning_event_payload(event, policy_path=policy_path, safe_roots=safe_roots)
    return validated.to_dict()


def record_learning_event(
    event: Any,
    *,
    dry_run: bool = True,
    db_path: Path | None = None,
    policy_path: Path | None = None,
    safe_roots: Sequence[Path] | None = None,
) -> dict:
    """Validate and optionally store a learning event in learning.sqlite3.

    Defaults to dry_run=True (fail-safe by default): validates the event
    against source allowlist, filesystem paths, and epistemic class rules,
    returning the validated event dictionary without mutating the store.
    Explicit dry_run=False performs the append-only write to learning.sqlite3.
    """
    from .learning_event import validate_learning_event_payload
    validated = validate_learning_event_payload(event, policy_path=policy_path, safe_roots=safe_roots)
    if dry_run:
        return {"dry_run": True, "validated": True, "event": validated.to_dict()}

    from .db import ensure_learning_event_schema
    from .learning_event import insert_learning_event
    path = db_path or DEFAULT_DB_PATH
    ensure_learning_event_schema(path)
    db = connect(path, write=True)
    try:
        insert_learning_event(db, validated)
        db.commit()
        return {"dry_run": False, "committed": True, "event_id": validated.id}
    finally:
        db.close()
def get_learning_event_by_id(event_id: str, *, db_path: Path | None = None) -> dict | None:
    """Retrieve a single learning event by id."""
    from .learning_event import get_learning_event
    path = db_path or DEFAULT_DB_PATH
    db = connect(path, write=False)
    try:
        event = get_learning_event(db, event_id)
        return event.to_dict() if event else None
    finally:
        db.close()


def record_error(
    *,
    objective_version_id: str,
    error_category: str,
    misconception: str,
    failed_step: str | None = None,
    verification_command: str | None = None,
    observed_output: str | None = None,
    expected_output: str | None = None,
    correction: str | None = None,
    attempt_id: str | None = None,
    actor: str = "user-provided",
    details: dict | None = None,
    db_path: Path | None = None,
) -> dict:
    """Record one error/misconception with provenance into the error ledger."""
    from . import error_ledger as err_mod
    from .db import ensure_error_ledger_schema

    path = db_path or DEFAULT_DB_PATH
    ensure_error_ledger_schema(path)
    db = connect(path, write=True)
    try:
        db.execute("BEGIN IMMEDIATE")
        entry = err_mod.record_error(
            db,
            objective_version_id=objective_version_id,
            error_category=error_category,
            misconception=misconception,
            failed_step=failed_step,
            verification_command=verification_command,
            observed_output=observed_output,
            expected_output=expected_output,
            correction=correction,
            attempt_id=attempt_id,
            actor=actor,
            details=details,
        )
        db.commit()
        return entry.to_dict()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def list_errors(
    *,
    objective_version_id: str | None = None,
    error_category: str | None = None,
    limit: int = 100,
    db_path: Path | None = None,
) -> list[dict]:
    """List error ledger entries."""
    from . import error_ledger as err_mod
    from .db import ensure_error_ledger_schema

    path = db_path or DEFAULT_DB_PATH
    ensure_error_ledger_schema(path)
    db = connect(path, write=False)
    try:
        entries = err_mod.list_errors(
            db,
            objective_version_id=objective_version_id,
            error_category=error_category,
            limit=limit,
        )
        return [e.to_dict() for e in entries]
    finally:
        db.close()


def error_summary(
    *,
    objective_version_id: str | None = None,
    db_path: Path | None = None,
) -> dict:
    """Summarize errors and misconceptions."""
    from . import error_ledger as err_mod
    from .db import ensure_error_ledger_schema

    path = db_path or DEFAULT_DB_PATH
    ensure_error_ledger_schema(path)
    db = connect(path, write=False)
    try:
        return err_mod.get_error_summary(db, objective_version_id=objective_version_id)
    finally:
        db.close()


def schedule_error_remediation(
    *,
    objective_version_id: str,
    error_id: str | None = None,
    attempt_id: str | None = None,
    misconception: str | None = None,
    rating: str = "AGAIN",
    db_path: Path | None = None,
) -> dict:
    """Schedule an immediate FSRS review remediation after an error."""
    from .db import ensure_review_event_schema
    from .spaced_retrieval import schedule_remediation_on_error

    path = db_path or DEFAULT_DB_PATH
    ensure_review_event_schema(path)
    db = connect(path, write=True)
    try:
        db.execute("BEGIN IMMEDIATE")
        receipt = schedule_remediation_on_error(
            db,
            objective_version_id=objective_version_id,
            error_id=error_id,
            attempt_id=attempt_id,
            misconception=misconception,
            rating=rating,
        )
        db.commit()
        return receipt
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def schedule_reviews_calendar(
    course_id: str,
    *,
    db_path: Path | None = None,
    study_window_start_hour: int = 18,
    study_window_end_hour: int = 21,
    session_duration_minutes: int = 30,
) -> list[dict]:
    """Fetch due reviews for a course and schedule them into calendar sessions."""
    from ..calendar_schedule import StudyWindow, schedule_study_sessions
    from .spaced_retrieval import get_due_reviews

    path = db_path or DEFAULT_DB_PATH
    db = connect(path, write=False)
    try:
        due_reviews = get_due_reviews(db, course_id)
        window = StudyWindow(
            start_hour=study_window_start_hour,
            end_hour=study_window_end_hour,
            session_duration_minutes=session_duration_minutes,
        )
        events = schedule_study_sessions(due_reviews, study_window=window)
        return [e.to_dict() for e in events]
    finally:
        db.close()


def export_reviews_ics(
    course_id: str,
    *,
    output_path: Path | str | None = None,
    db_path: Path | None = None,
) -> str:
    """Export due reviews as an RFC 5545 iCalendar (.ics) string or file."""
    from ..calendar_schedule import export_icalendar, schedule_study_sessions, write_icalendar
    from .spaced_retrieval import get_due_reviews

    path = db_path or DEFAULT_DB_PATH
    db = connect(path, write=False)
    try:
        due_reviews = get_due_reviews(db, course_id)
        events = schedule_study_sessions(due_reviews)
        if output_path:
            write_icalendar(events, output_path)
        return export_icalendar(events)
    finally:
        db.close()


def plan_canvas_pipeline(
    canvas_summary_path: Path | str,
    *,
    mode: str = "start-learning",
    db_path: Path | None = None,
    policy_path: Path | None = None,
    safe_roots: Sequence[Path] | None = None,
    commit_events: bool = False,
    max_assignments: int = 5,
) -> dict:
    """Execute end-to-end Canvas-to-learning-plan pipeline."""
    from ..canvas_pipeline import plan_canvas
    return plan_canvas(
        canvas_summary_path,
        mode=mode,
        db_path=db_path,
        policy_path=policy_path,
        safe_roots=safe_roots,
        commit_events=commit_events,
        max_assignments=max_assignments,
    )

def generate_case_study(
    *,
    event_id: Optional[str] = None,
    source: Optional[str] = None,
    require_verified: bool = True,
    db_path: Path | None = None,
) -> list[dict]:
    """Generate sanitized top-down systems engineering case studies from learning events."""
    from .case_study import generate_case_studies
    path = db_path or DEFAULT_DB_PATH
    db = connect(path, write=False)
    try:
        studies = generate_case_studies(
            db,
            event_id=event_id,
            source=source,
            require_verified=require_verified,
        )
        return [s.to_dict() for s in studies]
    finally:
        db.close()


def generate_quiz(
    *,
    course_id: Optional[str] = None,
    limit: int = 5,
    include_error_remediation: bool = True,
    db_path: Path | None = None,
) -> list[dict]:
    """Generate active-recall quiz questions from FSRS due reviews and error ledger."""
    from .quiz import generate_quiz_questions
    path = db_path or DEFAULT_DB_PATH
    db = connect(path, write=False)
    try:
        questions = generate_quiz_questions(
            db,
            course_id=course_id,
            limit=limit,
            include_error_remediation=include_error_remediation,
        )
        return [q.to_public_dict() for q in questions]
    finally:
        db.close()


def submit_quiz(
    *,
    question_dict: dict,
    user_answer: str,
    explicit_rating: Optional[str] = None,
    record_fsrs: bool = True,
    record_error_on_fail: bool = True,
    db_path: Path | None = None,
) -> dict:
    """Grade a single quiz question and update FSRS / error ledger.

    Caller-supplied `expected_answer` and `rubric_keywords` are untrusted and resolved
    server-side from the verified database entities.
    """
    from .quiz import resolve_quiz_rubric, submit_quiz_answer

    path = db_path or DEFAULT_DB_PATH
    db = connect(path, write=True)
    try:
        db.execute("BEGIN IMMEDIATE")
        resolved_question = resolve_quiz_rubric(db, question_dict)
        res = submit_quiz_answer(
            db,
            resolved_question,
            user_answer,
            explicit_rating=explicit_rating,
            record_fsrs=record_fsrs,
            record_error_on_fail=record_error_on_fail,
            resolve_rubric=False,
        )
        db.commit()
        return res.to_dict()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()

def progress_dashboard(
    *,
    course_id: Optional[str] = None,
    now: Optional[str] = None,
    db_path: Path | None = None,
) -> dict:
    """Generate comprehensive progress analytics report."""
    from .dashboard import generate_dashboard_report
    path = db_path or DEFAULT_DB_PATH
    db = connect(path, write=False)
    try:
        report = generate_dashboard_report(db, course_id=course_id, now=now)
        return report.to_dict()
    finally:
        db.close()


def render_dashboard_text(
    *,
    course_id: Optional[str] = None,
    now: Optional[str] = None,
    db_path: Path | None = None,
) -> str:
    """Generate clean ASCII terminal text dashboard."""
    from .dashboard import generate_dashboard_report
    path = db_path or DEFAULT_DB_PATH
    db = connect(path, write=False)
    try:
        report = generate_dashboard_report(db, course_id=course_id, now=now)
        return report.to_text()
    finally:
        db.close()


def get_calendar_status(*, hermes_dir: Optional[Path] = None) -> dict:
    """Check calendar credentials status safely."""
    from ..calendar_schedule import get_calendar_provider_status
    return get_calendar_provider_status(hermes_dir=hermes_dir).to_dict()


def sync_calendar(
    events: Sequence[Any],
    *,
    preferred_provider: Optional[str] = None,
    ics_output_path: Optional[Path | str] = None,
    hermes_dir: Optional[Path] = None,
) -> dict:
    """Sync events via CalendarSyncManager."""
    from ..calendar_schedule import CalendarSyncManager
    mgr = CalendarSyncManager(hermes_dir=hermes_dir)
    return mgr.sync(events, preferred_provider=preferred_provider, ics_output_path=ics_output_path)
