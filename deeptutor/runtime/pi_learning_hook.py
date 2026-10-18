"""Step C of docs/LEARNING-MAP.md: backend learning log + profile injection.

Two jobs, both backend-only (no UI, no new service):

* ``build_profile_text`` reads the owner's already-captured learner profile and the confirmed
  per-objective mastery states, and returns a short block for the next turn's system prompt.
* ``record_turn`` appends ONE ``learning_event`` per real chat turn (course, topic, question,
  answer, tool calls, time) through ``pi_learning`` -- the reused archive package, not a second DB.

Both are deliberately best-effort: a busy or missing learning store must never break a tutor turn,
and a missing fact is reported as "unknown", never as zero mastery (settled rule 4).
"""

from __future__ import annotations

import logging
import os
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

_PI_LEARNING = Path(__file__).resolve().parents[2] / "pi_learning"

# Wiki course folder code -> the Canvas course name deadlines.py already maps (extend this table, do
# not add fuzzy matching -- same rule as learning_tracking/academia_learning/deadlines.py).
_WIKI_CODE_TO_CANVAS_NAME = {
    "math2269": "Applied Bayesian Statistics",
    "isys1055": "Database Concepts",
    "math1298": "Analysis of Categorical Data",
}

_API = None


def _api():
    """Import pi_learning's api module once, or None when it is unavailable."""
    global _API
    if _API is None:
        if str(_PI_LEARNING) not in sys.path:
            sys.path.insert(0, str(_PI_LEARNING))
        try:
            from learning_tracking.academia_learning import api  # noqa: PLC0415

            _API = api
        except Exception as exc:  # pragma: no cover - environment dependent
            logger.warning("pi_learning unavailable, learning log disabled: %s", exc)
            return None
    return _API


def db_path() -> Path | None:
    api = _api()
    if api is None:
        return None
    override = os.environ.get("PI_LEARNING_DB")
    return Path(override) if override else Path(api.DEFAULT_DB_PATH)


def course_id_for(*texts: str) -> str | None:
    """Resolve a learning.sqlite3 course id from any text (message, wiki page path, tool args)."""
    api = _api()
    if api is None:
        return None
    blob = "\n".join(t for t in texts if t).lower()
    for code, canvas_name in _WIKI_CODE_TO_CANVAS_NAME.items():
        if code in blob:
            from learning_tracking.academia_learning import deadlines  # noqa: PLC0415

            try:
                db = api.connect(db_path(), write=False)
                try:
                    return deadlines.resolve_course_id(db, canvas_name)
                finally:
                    db.close()
            except Exception as exc:  # pragma: no cover - environment dependent
                logger.warning("course resolution failed: %s", exc)
                return None
    return None


def last_logged_course() -> str | None:
    """The course of the most recent logged turn, so continuity survives a question with no code."""
    api = _api()
    if api is None:
        return None
    try:
        db = api.connect(db_path(), write=False)
        try:
            row = db.execute(
                "SELECT subsystem FROM learning_event WHERE subsystem NOT IN ('unresolved-course', '') "
                "ORDER BY created_at DESC, rowid DESC LIMIT 1"
            ).fetchone()
            return row["subsystem"] if row else None
        finally:
            db.close()
    except Exception:
        return None


def build_profile_text(course_id: str | None, *, limit: int = 6) -> str:
    """Short learner block for the system prompt. Returns "" when nothing is known."""
    api = _api()
    if api is None:
        return ""
    try:
        course_id = course_id or last_logged_course()
        lines: list[str] = []
        profile = api.learner_profile(db_path=db_path())
        if profile:
            if profile.get("preferences"):
                lines.append("- Preferences: " + "; ".join(profile["preferences"][:3]))
            if profile.get("course_constraints"):
                lines.append("- Course constraints: " + "; ".join(profile["course_constraints"][:3]))
            adaptations = [
                a for ctx in profile.get("accessibility_context", []) for a in ctx.get("allowed_adaptations", [])
            ]
            if adaptations:
                lines.append("- Presentation only (never a mastery input): " + "; ".join(adaptations[:4]))
        from learning_tracking.academia_learning import objectives  # noqa: PLC0415

        db = api.connect(db_path(), write=False)
        try:
            # The live store keeps every seeded objective under one learner-evidence course, so the
            # detected course id is a preference, not a filter: read it first, then the rest.
            all_courses = [c["id"] for c in objectives.list_courses(db)]
            courses = ([course_id] if course_id else []) + [c for c in all_courses if c != course_id]
            shown = 0
            for cid in courses:
                if shown >= limit:
                    break
                for objective in objectives.list_objectives(db, cid):
                    if shown >= limit:
                        break
                    try:
                        progress = api.read_progress(objective.objective_version_id, db_path=db_path())
                        status = api.mastery_status(objective.objective_version_id, db_path=db_path())
                    except Exception:
                        continue
                    last = progress.get("last_evaluation") or {}
                    state = last.get("proposed_state") or "unknown"
                    if last and not progress.get("confirmed"):
                        state = f"{state} (unconfirmed)"
                    attempts = status.get("attempt_count")
                    estimate = status.get("p_mastery")
                    extra = f"computed p={estimate:.2f}" if isinstance(estimate, (int, float)) else "computed p=unknown"
                    lines.append(f"- {objective.definition[:80]}: {state}, {extra}, {attempts} attempts")
                    shown += 1
        finally:
            db.close()
        if not lines:
            return ""
        return (
            "Learner profile from the learning store (backend data, do not ask the owner for it; "
            "reading or chatting never creates mastery, so an unknown stays unknown):\n" + "\n".join(lines)
        )
    except Exception as exc:  # pragma: no cover - environment dependent
        logger.warning("profile build failed: %s", exc)
        return ""


def record_turn(
    *,
    session_id: str,
    question: str,
    answer: str,
    tool_calls: list[str],
    wiki_pages: list[str],
    course_id: str | None,
    topic: str | None,
    started_at: datetime,
    status: str,
) -> str | None:
    """Append one learning_event for this turn. Returns the event id, or None if it could not be stored."""
    api = _api()
    if api is None:
        return None
    now = datetime.now(timezone.utc)
    concept = topic or course_id or "chat turn"
    evidence = [
        {"source": "pi_tutor_turn", "reference": f"session:{session_id}:turn:{started_at.isoformat()}"},
    ]
    evidence += [{"source": "wiki", "reference": p} for p in dict.fromkeys(wiki_pages)][:3]
    event = {
        "learning_event": {
            "id": f"pi-turn-{started_at.strftime('%Y%m%dT%H%M%S')}-{uuid.uuid4().hex[:8]}",
            "source": "hermes_runtime",
            "sensitivity": "academic",
            "whole_system": "pi-tutor",
            "subsystem": course_id or "unresolved-course",
            "concept": concept,
            "incident_or_change_ref": f"session:{session_id}",
            "concept_tags": [t for t in (course_id, topic) if t],
            "observed_fact": (
                f"pi-tutor ran one chat turn: 1 question, {len(tool_calls)} tool calls "
                f"({', '.join(tool_calls) or 'none'}), {len(set(wiki_pages))} wiki pages read, "
                f"answer {len(answer)} characters, turn status {status}."
            ),
            "model_explanation": f"Question: {question[:400]}\nAnswer: {answer[:1500]}",
            "evidence": evidence,
            "lesson": {
                "concept": concept,
                "explanation": (
                    "Owner asked one question and read the tutor's answer; this is a record of the turn, "
                    "not assessed evidence, so mastery stays pending."
                ),
            },
            "exercise": {"task": "No exercise in this turn (question and answer only)."},
            "verification": {
                "command": f"pi_engine/turn.ts (bun) exit status {status}",
                "observed_output": f"{len(tool_calls)} tool calls; wiki pages: {', '.join(dict.fromkeys(wiki_pages)) or 'none'}",
            },
            "mastery": "pending",
            "sanitized_case_summary": False,
            "created_at": now.isoformat(),
        }
    }
    try:
        result = api.record_learning_event(event, dry_run=False, db_path=db_path())
    except Exception as exc:
        logger.warning("learning_event write failed: %s", exc)
        return None
    logger.info("learning_event %s recorded (%.1fs turn)", result.get("event_id"), (now - started_at).total_seconds())
    return result.get("event_id")
