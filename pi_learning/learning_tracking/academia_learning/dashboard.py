"""Learning dashboard and progress analytics service.

Provides a unified programmatic and CLI report across:
- Bayesian Knowledge Tracing (BKT) mastery probabilities.
- FSRS spaced retrieval review queues.
- Error taxonomy hotspots and misconceptions.
- Systems engineering learning events (breakdown by system/subsystem and sensitivity).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
from typing import Any, Dict, List, Optional

from .db import now_iso
from .error_ledger import get_error_summary as summarize_errors
from .learning_event import list_learning_events
from .mastery import GATE_THRESHOLD, compute_mastery


@dataclass(frozen=True)
class DashboardReport:
    generated_at: str
    mastery_summary: Dict[str, Any]
    spaced_retrieval_summary: Dict[str, Any]
    error_taxonomy_summary: Dict[str, Any]
    learning_events_summary: Dict[str, Any]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "generated_at": self.generated_at,
            "mastery": self.mastery_summary,
            "spaced_retrieval": self.spaced_retrieval_summary,
            "error_taxonomy": self.error_taxonomy_summary,
            "learning_events": self.learning_events_summary,
        }

    def to_text(self) -> str:
        lines = [
            "=" * 70,
            "                     HERMES LEARNING DASHBOARD",
            "=" * 70,
            f"Generated: {self.generated_at}",
            "",
            "--- [1] BKT MASTERY SUMMARY ---",
            f"  Total Objectives Evaluated: {self.mastery_summary['total_objectives']}",
            f"  Solid (P >= {GATE_THRESHOLD:.2f}):  {self.mastery_summary['solid_count']}",
            f"  In Progress:             {self.mastery_summary['in_progress_count']}",
            f"  Weak (P < 0.50):         {self.mastery_summary['weak_count']}",
            f"  Mean P(Mastery):         {self.mastery_summary['mean_p_mastery']:.2f}",
            "",
            "--- [2] FSRS SPACED RETRIEVAL QUEUES ---",
            f"  Total Reviews Recorded:  {self.spaced_retrieval_summary['total_reviews']}",
            f"  Reviews Due Now:         {self.spaced_retrieval_summary['due_count']}",
            f"  Upcoming (Future):       {self.spaced_retrieval_summary['upcoming_count']}",
            f"  Ratings Distribution:    {json.dumps(self.spaced_retrieval_summary['rating_counts'])}",
            "",
            "--- [3] ERROR TAXONOMY HOTSPOTS ---",
            f"  Total Recorded Errors:   {self.error_taxonomy_summary['total_errors']}",
            "  Top Error Categories:",
        ]
        cat_counts = (
            self.error_taxonomy_summary.get("by_category")
            or self.error_taxonomy_summary.get("category_counts")
            or {}
        )
        if cat_counts:
            for cat, count in sorted(cat_counts.items(), key=lambda x: -x[1])[:5]:
                lines.append(f"    - {cat}: {count}")
        else:
            lines.append("    (No errors recorded yet)")

        misconceptions = (
            self.error_taxonomy_summary.get("misconceptions")
            or self.error_taxonomy_summary.get("top_misconceptions")
            or []
        )
        if misconceptions:
            lines.append("  Top Misconceptions:")
            for m in misconceptions[:3]:
                lines.append(f"    * {m.get('misconception', '')[:60]}")

        lines.extend([
            "",
            "--- [4] SYSTEMS ENGINEERING LEARNING EVENTS ---",
            f"  Total Events:            {self.learning_events_summary['total_events']}",
            f"  Verified Incidents:      {self.learning_events_summary['verified_count']}",
            f"  Sanitized Case Studies:  {self.learning_events_summary['case_study_ready_count']}",
            "  Breakdown by System:",
        ])

        sys_counts = self.learning_events_summary.get("by_system", {})
        if sys_counts:
            for sys_name, count in sorted(sys_counts.items(), key=lambda x: -x[1]):
                lines.append(f"    - {sys_name}: {count}")
        else:
            lines.append("    (No learning events logged)")

        lines.append("  Sensitivity Breakdown:")
        sens_counts = self.learning_events_summary.get("by_sensitivity", {})
        for sens, count in sens_counts.items():
            lines.append(f"    - {sens}: {count}")

        lines.append("=" * 70)
        return "\n".join(lines)


def generate_dashboard_report(
    db: sqlite3.Connection,
    *,
    course_id: Optional[str] = None,
    now: Optional[str] = None,
) -> DashboardReport:
    """Compute and compile comprehensive progress analytics across all learning ledgers."""
    now_ts = now or now_iso()

    # 1. BKT Mastery aggregation
    obj_query = "SELECT id, course_id, definition FROM objective_version"
    params: List[Any] = []
    if course_id:
        obj_query += " WHERE course_id = ?"
    try:
        objectives = db.execute(obj_query, params).fetchall()
    except sqlite3.OperationalError:
        objectives = []
    total_objs = len(objectives)
    solid_count = 0
    weak_count = 0
    in_progress_count = 0
    p_mastery_sum = 0.0
    obj_details = []

    for obj in objectives:
        obj_id = obj[0]
        try:
            m = compute_mastery(db, obj_id)
            p = m.p_mastery
            gate_passed = m.gate_passed
            reason = m.reason
            att_cnt = m.attempt_count
        except (sqlite3.OperationalError, sqlite3.DatabaseError):
            p = 0.0
            gate_passed = False
            reason = "DB error loading mastery"
            att_cnt = 0

        p_mastery_sum += p
        if gate_passed:
            solid_count += 1
        elif p < 0.50:
            weak_count += 1
        else:
            in_progress_count += 1

        obj_details.append({
            "objective_id": obj_id,
            "definition": obj[2] if len(obj) > 2 else "",
            "p_mastery": round(p, 3),
            "gate_passed": gate_passed,
            "reason": reason,
            "attempts": att_cnt,
        })

    mean_p = round(p_mastery_sum / total_objs, 3) if total_objs > 0 else 0.0

    mastery_summary = {
        "total_objectives": total_objs,
        "solid_count": solid_count,
        "in_progress_count": in_progress_count,
        "weak_count": weak_count,
        "mean_p_mastery": mean_p,
        "gate_threshold": GATE_THRESHOLD,
        "objectives": obj_details,
    }

    # 2. Spaced Retrieval (FSRS) summary
    try:
        sr_rows = db.execute(
            """
            SELECT id, objective_version_id, rating, verified_result,
                   next_interval_days, next_review_at
            FROM review_event
            ORDER BY reviewed_at DESC
            """
        ).fetchall()
    except Exception:
        sr_rows = []

    total_reviews = len(sr_rows)
    due_count = 0
    upcoming_count = 0
    rating_counts = {"AGAIN": 0, "HARD": 0, "GOOD": 0, "EASY": 0}

    # Find distinct latest card per objective
    seen_objs = set()
    for row in sr_rows:
        rating = row["rating"]
        if rating in rating_counts:
            rating_counts[rating] += 1
        obj_id = row["objective_version_id"]
        if obj_id not in seen_objs:
            seen_objs.add(obj_id)
            next_due = row["next_review_at"]
            if next_due:
                if next_due <= now_ts:
                    due_count += 1
                else:
                    upcoming_count += 1

    spaced_retrieval_summary = {
        "total_reviews": total_reviews,
        "active_cards": len(seen_objs),
        "due_count": due_count,
        "upcoming_count": upcoming_count,
        "rating_counts": rating_counts,
    }

    # 3. Error taxonomy hotspots
    try:
        error_summary = dict(summarize_errors(db))
    except sqlite3.OperationalError:
        error_summary = {"total_errors": 0, "by_category": {}, "misconceptions": [], "failed_steps": []}
    if "by_category" in error_summary and "category_counts" not in error_summary:
        error_summary["category_counts"] = error_summary["by_category"]

    # 4. Systems engineering learning events breakdown
    try:
        events = list_learning_events(db)
    except sqlite3.OperationalError:
        events = []
    by_system: Dict[str, int] = {}
    by_subsystem: Dict[str, int] = {}
    by_sensitivity: Dict[str, int] = {}
    verified_cnt = 0
    case_study_ready_cnt = 0

    for ev in events:
        sys_key = ev.whole_system
        sub_key = f"{ev.whole_system}:{ev.subsystem}"
        sens_key = ev.sensitivity

        by_system[sys_key] = by_system.get(sys_key, 0) + 1
        by_subsystem[sub_key] = by_subsystem.get(sub_key, 0) + 1
        by_sensitivity[sens_key] = by_sensitivity.get(sens_key, 0) + 1

        if ev.verified_result:
            verified_cnt += 1
        if ev.sanitized_case_summary or ev.verified_result:
            case_study_ready_cnt += 1

    learning_events_summary = {
        "total_events": len(events),
        "verified_count": verified_cnt,
        "case_study_ready_count": case_study_ready_cnt,
        "by_system": by_system,
        "by_subsystem": by_subsystem,
        "by_sensitivity": by_sensitivity,
    }

    return DashboardReport(
        generated_at=now_ts,
        mastery_summary=mastery_summary,
        spaced_retrieval_summary=spaced_retrieval_summary,
        error_taxonomy_summary=error_summary,
        learning_events_summary=learning_events_summary,
    )
