"""End-to-end Canvas assignment to learning plan pipeline.

Connects:
Canvas assignments -> Concept mapping -> Study blocks / Learning plan -> Learning events / Review tasks
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
import re
from typing import Any, Dict, List, Optional, Sequence
from .academia_learning.db import (
    DEFAULT_DB_PATH,
    connect,
    ensure_learning_event_schema,
    ensure_review_event_schema,
)
from .academia_learning.learning_event import (
    LearningEvent,
    SourceSensitivity,
    insert_learning_event,
    validate_learning_event_payload,
)
from .calendar_schedule import CalendarEvent, export_icalendar, schedule_study_sessions, write_icalendar
from .canvas import load_assignments
from .concepts import concept_terms
from .examples import Example, find_example
from .models import Assignment
from .planner import build_plan, rank_assignments


DEFAULT_EXAMPLE_ROOTS = [
    Path("/home/phroggingphrog/projects/hermes-data-system/worktrees/analytics-ops-v1/src"),
    Path("/home/phroggingphrog/projects/hermes-tools"),
    Path("/home/phroggingphrog/.hermes/hermes-agent"),
]


def sanitize_trigger_text(text: str) -> str:
    """Sanitize text for Section 14 triggers by removing newlines, replacing '|' with '-', and stripping non-ASCII characters."""
    if not text:
        return ""
    cleaned = text.replace("\r", "").replace("\n", "").replace("|", "-")
    return re.sub(r"[^\x20-\x7E]", "", cleaned)


@dataclass(frozen=True)
class ReviewTask:
    task_id: str
    assignment_id: str
    title: str
    concept: str
    scheduled_for: datetime
    task_type: str  # "initial_study" | "spaced_review" | "final_verification"
    action: str

    def to_remind_trigger(self, list_name: str = "Study") -> str:
        """Section 14: Timed reminder in Apple Reminders (REMIND: <Time> | <Title> | <List>)."""
        time_str = self.scheduled_for.strftime("%Y-%m-%d %H:%M")
        title = sanitize_trigger_text(self.title)
        lname = sanitize_trigger_text(list_name)
        return f"REMIND: {time_str} | {title} | {lname}"

    def to_timer_trigger(self, minutes: int = 45) -> str:
        """Section 14: Background timer on iOS (TIMER: <Minutes> | <Label>)."""
        if isinstance(minutes, bool) or not isinstance(minutes, int) or not (1 <= minutes <= 1440):
            raise ValueError(f"Timer minutes must be an integer between 1 and 1440, got {minutes!r}")
        title = sanitize_trigger_text(self.title)
        return f"TIMER: {minutes} | {title}"

    def to_popup_trigger(self, message: Optional[str] = None) -> str:
        """Section 14: Intrusive modal alert on iOS (POPUP: <Title> | <Message>)."""
        msg = sanitize_trigger_text(message or self.action)
        title = sanitize_trigger_text(self.title)
        return f"POPUP: {title} | {msg}"

    def to_todo_trigger(self, list_name: str = "Study") -> str:
        """Section 14: To-Do checklist item in Apple Reminders (TODO: <List> | <Item>)."""
        lname = sanitize_trigger_text(list_name)
        action = sanitize_trigger_text(self.action)
        return f"TODO: {lname} | {action}"

    def to_alarm_trigger(self, alarm_time: Optional[str] = None) -> str:
        """Section 14: Clock app alarm (ALARM: <HH:MM> | <Label>)."""
        t_str = alarm_time or self.scheduled_for.strftime("%H:%M")
        if not re.match(r"^(?:[01]\d|2[0-3]):[0-5]\d$", t_str):
            raise ValueError(f"Invalid alarm time format: {t_str!r}, expected HH:MM (24-hour)")
        title = sanitize_trigger_text(self.title)
        return f"ALARM: {t_str} | {title}"

    def to_urgent_trigger(self, message: Optional[str] = None) -> str:
        """Section 14: Critical push banner + Siri speak text (URGENT: <Title> | <Msg>)."""
        msg_raw = message or f"{self.title} scheduled for {self.scheduled_for.strftime('%H:%M')}"
        title = sanitize_trigger_text(self.title)
        msg = sanitize_trigger_text(msg_raw)
        return f"URGENT: {title} | {msg}"
    def to_dict(self) -> Dict[str, Any]:
        return {
            "task_id": self.task_id,
            "assignment_id": self.assignment_id,
            "title": self.title,
            "concept": self.concept,
            "scheduled_for": self.scheduled_for.isoformat(),
            "task_type": self.task_type,
            "action": self.action,
            "section14_trigger": self.to_remind_trigger(),
            "section14_triggers": {
                "remind": self.to_remind_trigger(),
                "timer": self.to_timer_trigger(),
                "popup": self.to_popup_trigger(),
                "todo": self.to_todo_trigger(),
                "alarm": self.to_alarm_trigger(),
                "urgent": self.to_urgent_trigger(),
            },
        }


def generate_review_tasks(
    assignment: Assignment,
    primary_concept: str,
    *,
    now: Optional[datetime] = None,
) -> List[ReviewTask]:
    """Generate spaced review tasks leading up to the personal deadline."""
    if assignment.due_at is None or assignment.personal_final is None:
        return []
    ref_now = now or datetime.now(timezone.utc)
    tasks: List[ReviewTask] = []

    # Task 1: Initial Study & Problem Exploration (Immediate / Day 0)
    tasks.append(
        ReviewTask(
            task_id=f"task-{assignment.id}-initial",
            assignment_id=assignment.id,
            title=f"Study {primary_concept} for {assignment.title}",
            concept=primary_concept,
            scheduled_for=ref_now,
            task_type="initial_study",
            action=f"Deep dive into {primary_concept} and produce first draft artifact for {assignment.title}.",
        )
    )

    # Task 2: Active Recall & Misconception Check (Halfway to personal deadline)
    deadline = assignment.personal_final
    if deadline.tzinfo is None:
        deadline = deadline.replace(tzinfo=timezone.utc)

    if deadline > ref_now:
        midpoint = ref_now + (deadline - ref_now) / 2
        tasks.append(
            ReviewTask(
                task_id=f"task-{assignment.id}-mid-review",
                assignment_id=assignment.id,
                title=f"Spaced Recall: {primary_concept}",
                concept=primary_concept,
                scheduled_for=midpoint,
                task_type="spaced_review",
                action=f"Review error ledger, test understanding of {primary_concept}, verify artifact constraints.",
            )
        )

        # Task 3: Final Verification (24h before personal deadline)
        final_check_time = deadline - timedelta(days=1)
        if final_check_time > midpoint:
            tasks.append(
                ReviewTask(
                    task_id=f"task-{assignment.id}-final-check",
                    assignment_id=assignment.id,
                    title=f"Final Verification: {assignment.title}",
                    concept=primary_concept,
                    scheduled_for=final_check_time,
                    task_type="final_verification",
                    action=f"Execute verification commands, audit deliverables against requirements, submit final artifact.",
                )
            )

    return tasks


def plan_canvas(
    canvas_summary_path: Optional[Path | str] = None,
    *,
    fetch_live: bool = False,
    mode: str = "start-learning",
    example_roots: Optional[Sequence[Path]] = None,
    db_path: Optional[Path | str] = None,
    policy_path: Optional[Path | str] = None,
    safe_roots: Optional[Sequence[Path]] = None,
    commit_events: bool = False,
    max_assignments: int = 5,
    max_files: int = 200,
    max_bytes: int = 1_000_000,
) -> Dict[str, Any]:
    """Execute end-to-end pipeline from Canvas assignments to learning plan."""
    assignments = load_assignments(canvas_summary_path, fetch_live=fetch_live)
    if not assignments:
        return {
            "status": "EMPTY",
            "assignments": [],
            "study_blocks": [],
            "learning_events": [],
            "review_tasks": [],
            "calendar_events": [],
            "section14_triggers": [],
        }

    ranked = rank_assignments(assignments)[:max_assignments]
    roots = list(example_roots) if example_roots is not None else DEFAULT_EXAMPLE_ROOTS

    study_blocks: List[Dict[str, Any]] = []
    learning_events: List[LearningEvent] = []
    review_tasks: List[ReviewTask] = []

    for rank_idx, assignment in enumerate(ranked):
        concepts = concept_terms(assignment.course, assignment.title, assignment.prerequisites)
        primary_concept = concepts[0] if concepts else (assignment.title or "general")
        query = " ".join(concepts)

        example = find_example(
            query,
            roots,
            max_files=max_files,
            max_bytes=max_bytes,
        )

        plan = build_plan(assignment, mode=mode, example=example)
        plan["rank"] = rank_idx + 1
        due_at_iso = assignment.due_at.isoformat() if assignment.due_at is not None else None
        personal_final_iso = assignment.personal_final.isoformat() if assignment.personal_final is not None else None
        original_due_at_iso = (
            assignment.original_due_at.isoformat() if assignment.original_due_at is not None else None
        )
        extended_due_at_iso = (
            assignment.extended_due_at.isoformat() if assignment.extended_due_at is not None else None
        )
        plan["assignment"] = {
            "id": assignment.id,
            "title": assignment.title,
            "course": assignment.course,
            "due_at": due_at_iso,
            "personal_final": personal_final_iso,
            "original_due_at": original_due_at_iso,
            "extended_due_at": extended_due_at_iso,
            "progress": assignment.progress,
        }
        study_blocks.append(plan)

        # Build formal LearningEvent for tracking
        block_info = plan.get("block", {})
        clean_concept = primary_concept.replace(" ", "_")
        le_id = f"canvas-{assignment.id}-{clean_concept}-{rank_idx}"
        if assignment.due_at is not None and assignment.personal_final is not None:
            observed_fact = (
                f"Canvas assignment due {assignment.due_at.strftime('%Y-%m-%d')}, "
                f"personal final {assignment.personal_final.strftime('%Y-%m-%d')}."
            )
        else:
            observed_fact = f"Canvas assignment {assignment.title} (undated)."

        le = LearningEvent(
            id=le_id,
            source="canvas_formal",
            sensitivity="academic",
            whole_system=assignment.course or "University",
            subsystem=assignment.title or "Assignment",
            concept=primary_concept,
            concept_tags=list(concepts),
            observed_fact=observed_fact,
            evidence=[{
                "source": "canvas_formal",
                "reference": f"assignment:{assignment.id}:{assignment.title}",
            }],
            lesson={
                "concept": primary_concept,
                "explanation": f"Study block for {assignment.title} focusing on {primary_concept}.",
            },
            exercise={
                "task": block_info.get("learner_problem") or f"Complete {assignment.title}",
                "artifact_check": block_info.get("artifact_check") or "Produce reviewable artifact",
            },
            verification={
                "command": block_info.get("understanding_test") or "Explain key choices without notes",
            },
            mastery="pending",
        )
        learning_events.append(le)

        # Generate spaced review tasks
        if assignment.due_at is not None and assignment.personal_final is not None:
            tasks = generate_review_tasks(assignment, primary_concept)
            review_tasks.extend(tasks)
    # If requested, commit learning events to learning.sqlite3
    committed_ids: List[str] = []
    if commit_events:
        target_policy = Path(policy_path) if policy_path is not None else None
        target_safe_roots = list(safe_roots) if safe_roots is not None else None
        validated_events = [
            validate_learning_event_payload(
                le,
                policy_path=target_policy,
                safe_roots=target_safe_roots,
            )
            for le in learning_events
        ]
        target_db = Path(db_path) if db_path else DEFAULT_DB_PATH
        ensure_learning_event_schema(target_db)
        db = connect(target_db, write=True)
        try:
            for le in validated_events:
                cur = db.execute("SELECT 1 FROM learning_event WHERE id = ?", (le.id,))
                if cur.fetchone() is None:
                    insert_learning_event(db, le)
                committed_ids.append(le.id)
            db.commit()
        finally:
            db.close()

    # Generate calendar events for review tasks
    schedule_items = [
        {
            "title": f"Study: {t.title}",
            "description": f"{t.action}\nConcept: {t.concept}\nTask ID: {t.task_id}",
            "scheduled_for": t.scheduled_for,
            "duration_minutes": 30,
        }
        for t in review_tasks
    ]
    calendar_events = schedule_study_sessions(schedule_items)

    # Generate Section 14 outbound mobile dispatch triggers
    section14_triggers: List[str] = []
    for t in review_tasks:
        section14_triggers.append(t.to_remind_trigger())
    if review_tasks:
        section14_triggers.append(review_tasks[0].to_timer_trigger(45))
        section14_triggers.append(review_tasks[0].to_popup_trigger("Study session ready to start."))

    return {
        "status": "OK",
        "assignments": [s["assignment"] for s in study_blocks],
        "study_blocks": study_blocks,
        "learning_events": [le.to_dict() for le in learning_events],
        "review_tasks": [t.to_dict() for t in review_tasks],
        "calendar_events": [ev.to_dict() for ev in calendar_events],
        "section14_triggers": section14_triggers,
        "committed_events": committed_ids,
    }
