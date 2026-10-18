"""Active-recall quiz generator and spaced-retrieval review driver.

Integrates FSRS spaced retrieval (via review_event and py-fsrs), explicit
error ledger misconceptions, and curriculum concepts into prompt-response
diagnostic challenges with immediate grading and FSRS interval updates.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
from typing import Any, Dict, List, Optional, Sequence, Tuple
import uuid

from .db import now_iso
from .error_ledger import list_errors as list_error_ledger_entries, record_error
from .learning_event import list_learning_events
from .spaced_retrieval import get_due_reviews, record_review


@dataclass(frozen=True)
class QuizQuestion:
    id: str
    objective_version_id: Optional[str]
    question_type: str  # "active_recall", "diagnostic_remediation", "systems_verification"
    concept: str
    prompt: str
    expected_answer: str
    rubric_keywords: tuple[str, ...]
    metadata: Dict[str, Any]

    def to_dict(self, *, include_rubric: bool = False) -> Dict[str, Any]:
        data = {
            "id": self.id,
            "objective_version_id": self.objective_version_id,
            "question_type": self.question_type,
            "concept": self.concept,
            "prompt": self.prompt,
            "metadata": self.metadata,
        }
        if include_rubric:
            data["expected_answer"] = self.expected_answer
            data["rubric_keywords"] = list(self.rubric_keywords)
        return data

    def to_public_dict(self) -> Dict[str, Any]:
        return self.to_dict(include_rubric=False)

    def to_full_dict(self) -> Dict[str, Any]:
        return self.to_dict(include_rubric=True)


@dataclass(frozen=True)
class QuizAttemptResult:
    question_id: str
    objective_version_id: Optional[str]
    user_answer: str
    score: float
    is_correct: bool
    rating: str
    feedback: str
    next_interval_days: Optional[float]
    next_review_at: Optional[str]
    error_recorded: bool
    expected_answer: Optional[str] = None
    rubric_keywords: tuple[str, ...] = ()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "question_id": self.question_id,
            "objective_version_id": self.objective_version_id,
            "user_answer": self.user_answer,
            "score": self.score,
            "is_correct": self.is_correct,
            "rating": self.rating,
            "feedback": self.feedback,
            "next_interval_days": self.next_interval_days,
            "next_review_at": self.next_review_at,
            "error_recorded": self.error_recorded,
            "expected_answer": self.expected_answer,
            "rubric_keywords": list(self.rubric_keywords) if self.rubric_keywords else [],
        }


@dataclass(frozen=True)
class QuizSessionSummary:
    session_id: str
    total_questions: int
    correct_count: int
    average_score: float
    results: List[QuizAttemptResult]
    completed_at: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "session_id": self.session_id,
            "total_questions": self.total_questions,
            "correct_count": self.correct_count,
            "average_score": self.average_score,
            "results": [r.to_dict() for r in self.results],
            "completed_at": self.completed_at,
        }


def generate_quiz_questions(
    db: sqlite3.Connection,
    *,
    course_id: Optional[str] = None,
    limit: int = 5,
    include_error_remediation: bool = True,
) -> List[QuizQuestion]:
    """Generate active-recall and diagnostic questions from FSRS due reviews and error ledger."""
    questions: List[QuizQuestion] = []

    # 1. Check error ledger for remediation hotspots
    if include_error_remediation:
        try:
            errors = list_error_ledger_entries(db)
        except sqlite3.OperationalError:
            errors = []
        for err in reversed(errors):
            if len(questions) >= limit:
                break
            q_id = f"q-err-{err.id[:8]}"
            prompt = (
                f"[Diagnostic Remediation] Concept relates to error category '{err.error_category}'.\n"
                f"Identified Misconception: {err.misconception}\n"
                f"Failed Step: {err.failed_step or 'N/A'}\n"
                f"Question: Explain the root cause of this failure and the necessary corrective invariant."
            )
            expected = err.correction or f"Avoid {err.misconception} by enforcing expected invariants."
            keywords = tuple(
                dict.fromkeys(
                    w.strip(".,;:()[]{}\"'").lower()
                    for w in ((err.correction or "") + " " + err.misconception).split()
                    if len(w.strip(".,;:()[]{}\"'")) > 3
                )
            )[:8]
            questions.append(
                QuizQuestion(
                    id=q_id,
                    objective_version_id=err.objective_version_id,
                    question_type="diagnostic_remediation",
                    concept=err.error_category,
                    prompt=prompt,
                    expected_answer=expected,
                    rubric_keywords=keywords,
                    metadata={"error_id": err.id, "category": err.error_category},
                )
            )

    # 2. Check FSRS due reviews if course_id or general objectives
    if len(questions) < limit and course_id:
        due_reviews = get_due_reviews(db, course_id)
        for r in due_reviews:
            if len(questions) >= limit:
                break
            obj_id = r["objective_version_id"]
            # Look up objective definition
            row = db.execute(
                "SELECT definition FROM objective_version WHERE id = ?", (obj_id,)
            ).fetchone()
            definition = row[0] if row else obj_id
            q_id = f"q-fsrs-{obj_id[:8]}"
            prompt = (
                f"[Active Recall - Spaced Review]\n"
                f"Objective: {definition}\n"
                f"Explain how this concept applies in systems engineering and state its core invariant."
            )
            keywords = tuple(w.lower() for w in definition.split() if len(w) > 4)[:5]
            questions.append(
                QuizQuestion(
                    id=q_id,
                    objective_version_id=obj_id,
                    question_type="active_recall",
                    concept=definition,
                    prompt=prompt,
                    expected_answer=definition,
                    rubric_keywords=keywords,
                    metadata={"due_date": r.get("due"), "source": "fsrs_due"},
                )
            )

    # 3. If still below limit, pull from learning events
    if len(questions) < limit:
        try:
            events = list_learning_events(db)
        except sqlite3.OperationalError:
            events = []
        for ev in reversed(events):
            if len(questions) >= limit:
                break
            q_id = f"q-event-{ev.id[:8]}"
            prompt = (
                f"[Systems Engineering Review]\n"
                f"System: {ev.whole_system} -> Subsystem: {ev.subsystem}\n"
                f"Concept: {ev.concept}\n"
                f"Exercise: {ev.exercise.get('task', '')}\n"
                f"What verification command confirms this behavior?"
            )
            expected = ev.verification.get("command", "")
            keywords = tuple(
                w.lower()
                for w in (ev.concept + " " + expected).split()
                if len(w) > 3
            )[:5]
            questions.append(
                QuizQuestion(
                    id=q_id,
                    objective_version_id=None,
                    question_type="systems_verification",
                    concept=ev.concept,
                    prompt=prompt,
                    expected_answer=expected,
                    rubric_keywords=keywords,
                    metadata={"event_id": ev.id, "system": ev.whole_system},
                )
            )

    return questions

def resolve_quiz_rubric(
    db: sqlite3.Connection,
    question: QuizQuestion | Dict[str, Any],
) -> QuizQuestion:
    """Resolve a quiz question's expected answer, rubric keywords, and objective server-side.

    Untrusted caller-supplied `expected_answer` and `rubric_keywords` are discarded to
    prevent answer spoofing and FSRS review forgery.
    """
    if isinstance(question, QuizQuestion):
        q_id = question.id
        obj_id = question.objective_version_id
        q_type = question.question_type
        meta = dict(question.metadata) if question.metadata else {}
    else:
        q_id = str(question.get("id", ""))
        obj_id = question.get("objective_version_id")
        q_type = question.get("question_type", "")
        meta = dict(question.get("metadata", {}))

    # 1. Error Ledger resolution (Diagnostic Remediation)
    err_id = meta.get("error_id")
    if not err_id and q_id.startswith("q-err-"):
        err_id = q_id[len("q-err-"):]
    if err_id or q_type == "diagnostic_remediation":
        row = None
        if err_id:
            row = db.execute(
                "SELECT id, objective_version_id, error_category, misconception, failed_step, correction FROM error_ledger WHERE id = ? OR id LIKE ?",
                (err_id, f"{err_id}%"),
            ).fetchone()
        elif obj_id:
            row = db.execute(
                "SELECT id, objective_version_id, error_category, misconception, failed_step, correction FROM error_ledger WHERE objective_version_id = ? ORDER BY recorded_at DESC",
                (obj_id,),
            ).fetchone()

        if row:
            err_pk, db_obj_id, error_category, misconception, failed_step, correction = row
            prompt = (
                f"[Diagnostic Remediation] Concept relates to error category '{error_category}'.\n"
                f"Identified Misconception: {misconception}\n"
                f"Failed Step: {failed_step or 'N/A'}\n"
                f"Question: Explain the root cause of this failure and the necessary corrective invariant."
            )
            expected = correction or f"Avoid {misconception} by enforcing expected invariants."
            keywords = tuple(
                dict.fromkeys(
                    w.strip(".,;:()[]{}\"'").lower()
                    for w in ((correction or "") + " " + misconception).split()
                    if len(w.strip(".,;:()[]{}\"'")) > 3
                )
            )[:8]
            return QuizQuestion(
                id=q_id or f"q-err-{err_pk[:8]}",
                objective_version_id=db_obj_id,
                question_type="diagnostic_remediation",
                concept=error_category,
                prompt=prompt,
                expected_answer=expected,
                rubric_keywords=keywords,
                metadata={"error_id": err_pk, "category": error_category},
            )

    # 2. Objective Version resolution (Active Recall / FSRS)
    target_obj = meta.get("objective_id") or obj_id
    if not target_obj and q_id.startswith("q-fsrs-"):
        target_obj = q_id[len("q-fsrs-"):]
    if target_obj or q_type == "active_recall":
        row = None
        if target_obj:
            row = db.execute(
                "SELECT id, definition FROM objective_version WHERE id = ? OR id LIKE ?",
                (target_obj, f"{target_obj}%"),
            ).fetchone()
        if row:
            db_obj_id, definition = row[0], row[1]
            prompt = (
                f"[Active Recall - Spaced Review]\n"
                f"Objective: {definition}\n"
                f"Explain how this concept applies in systems engineering and state its core invariant."
            )
            keywords = tuple(w.lower() for w in definition.split() if len(w) > 4)[:5]
            return QuizQuestion(
                id=q_id or f"q-fsrs-{db_obj_id[:8]}",
                objective_version_id=db_obj_id,
                question_type="active_recall",
                concept=definition,
                prompt=prompt,
                expected_answer=definition,
                rubric_keywords=keywords,
                metadata={"objective_id": db_obj_id, "source": "fsrs_due"},
            )

    # 3. Learning Event resolution (Systems Verification)
    target_ev = meta.get("event_id")
    if not target_ev and q_id.startswith("q-event-"):
        target_ev = q_id[len("q-event-"):]
    if target_ev or q_type == "systems_verification":
        from .learning_event import get_learning_event, list_learning_events
        ev = None
        if target_ev:
            ev = get_learning_event(db, target_ev)
            if not ev:
                events = list_learning_events(db)
                ev = next((e for e in events if e.id.startswith(target_ev)), None)
        if ev:
            prompt = (
                f"[Systems Engineering Review]\n"
                f"System: {ev.whole_system} -> Subsystem: {ev.subsystem}\n"
                f"Concept: {ev.concept}\n"
                f"Exercise: {ev.exercise.get('task', '')}\n"
                f"What verification command confirms this behavior?"
            )
            expected = ev.verification.get("command", "")
            keywords = tuple(
                w.lower()
                for w in (ev.concept + " " + expected).split()
                if len(w) > 3
            )[:5]
            return QuizQuestion(
                id=q_id or f"q-event-{ev.id[:8]}",
                objective_version_id=None,
                question_type="systems_verification",
                concept=ev.concept,
                prompt=prompt,
                expected_answer=expected,
                rubric_keywords=keywords,
                metadata={"event_id": ev.id, "source": "learning_event"},
            )

    raise ValueError(
        f"Quiz question '{q_id}' cannot be resolved server-side from database: "
        "no matching error ledger, objective version, or learning event found. "
        "Caller-supplied rubrics and answers are not trusted."
    )
def grade_quiz_answer(
    question: QuizQuestion,
    user_answer: str,
) -> Tuple[float, bool, str]:
    """Grade a quiz answer deterministically using keyword overlap and exact matches."""
    cleaned_user = user_answer.strip().lower()
    cleaned_expected = question.expected_answer.strip().lower()

    if not cleaned_user:
        return 0.0, False, "No answer provided."

    # Direct match or exact substring
    if cleaned_user == cleaned_expected or cleaned_expected in cleaned_user:
        return 1.0, True, "Full credit: exact match / complete coverage."

    # Keyword overlap scoring
    expected_words = [
        w.strip(".,;:()[]{}\"'").lower()
        for w in question.expected_answer.split()
        if len(w.strip(".,;:()[]{}\"'")) > 3
    ]
    all_target_keywords = list(dict.fromkeys(list(question.rubric_keywords) + expected_words))
    if all_target_keywords:
        matched = sum(1 for kw in all_target_keywords if kw in cleaned_user)
        denom = max(1, len(expected_words) if expected_words else len(all_target_keywords))
        score = min(1.0, round(matched / denom, 2))
        is_correct = score >= 0.5
        feedback = f"Matched {matched}/{len(all_target_keywords)} key concepts."
        return score, is_correct, feedback


def submit_quiz_answer(
    db: sqlite3.Connection,
    question: QuizQuestion,
    user_answer: str,
    *,
    explicit_rating: Optional[str] = None,
    record_fsrs: bool = True,
    record_error_on_fail: bool = True,
    resolve_rubric: bool = True,
) -> QuizAttemptResult:
    """Grade an answer, update FSRS spaced retrieval, and optionally log to error ledger."""
    if resolve_rubric:
        question = resolve_quiz_rubric(db, question)
    score, is_correct, feedback = grade_quiz_answer(question, user_answer)

    # Determine FSRS rating
    if explicit_rating in {"AGAIN", "HARD", "GOOD", "EASY"}:
        rating = explicit_rating
    elif score >= 0.85:
        rating = "EASY"
    elif score >= 0.65:
        rating = "GOOD"
    elif score >= 0.40:
        rating = "HARD"
    else:
        rating = "AGAIN"

    next_interval: Optional[float] = None
    next_due_str: Optional[str] = None

    # Update FSRS spaced retrieval if tied to an objective
    if record_fsrs and question.objective_version_id:
        try:
            review_rec = record_review(
                db,
                objective_version_id=question.objective_version_id,
                attempt_id=None,
                rating=rating,
                verified_result="CORRECT" if is_correct else "INCORRECT",
                assistance_used=False,
                evidence_refs=[f"quiz:{question.id}"],
            )
            next_interval = review_rec["next_interval_days"]
            next_due_str = review_rec["next_review_at"]
        except Exception:
            # Fallback if objective not registered in FK-strict tables
            pass

    # Record in error ledger if failed
    error_recorded = False
    if not is_correct and record_error_on_fail and question.objective_version_id:
        try:
            record_error(
                db,
                objective_version_id=question.objective_version_id,
                error_category="verification_failed",
                misconception=f"Failed quiz challenge: {question.concept}",
                failed_step=question.prompt[:100],
                expected_output=question.expected_answer[:200],
                observed_output=user_answer[:200],
                correction=f"Review concept invariants: {question.expected_answer[:150]}",
                actor="quiz_evaluator",
            )
            error_recorded = True
        except Exception:
            pass

    return QuizAttemptResult(
        question_id=question.id,
        objective_version_id=question.objective_version_id,
        user_answer=user_answer,
        score=score,
        is_correct=is_correct,
        rating=rating,
        feedback=feedback,
        next_interval_days=next_interval,
        next_review_at=next_due_str,
        error_recorded=error_recorded,
        expected_answer=question.expected_answer,
        rubric_keywords=question.rubric_keywords,
    )


def run_quiz_session(
    db: sqlite3.Connection,
    questions: Sequence[QuizQuestion],
    answers: Dict[str, str],
) -> QuizSessionSummary:
    """Execute a batch quiz session across questions and answers."""
    results: List[QuizAttemptResult] = []
    correct_count = 0
    total_score = 0.0

    for q in questions:
        user_ans = answers.get(q.id, "")
        res = submit_quiz_answer(db, q, user_ans)
        results.append(res)
        if res.is_correct:
            correct_count += 1
        total_score += res.score

    avg_score = round(total_score / len(questions), 2) if questions else 0.0

    return QuizSessionSummary(
        session_id=f"session-{uuid.uuid4().hex[:8]}",
        total_questions=len(questions),
        correct_count=correct_count,
        average_score=avg_score,
        results=results,
        completed_at=now_iso(),
    )
