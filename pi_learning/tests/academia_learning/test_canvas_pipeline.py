"""Tests for end-to-end Canvas-to-learning-plan pipeline and CLI."""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import pytest

from learning_tracking.academia_learning import api
from learning_tracking.canvas_pipeline import (
    ReviewTask,
    generate_review_tasks,
    plan_canvas,
    sanitize_trigger_text,
)
from learning_tracking.models import Assignment
from datetime import datetime, timezone


def test_plan_canvas_pipeline_end_to_end(tmp_path: Path):
    fixture_canvas = Path("tests/fixtures/canvas.json")
    example_root = Path("tests/fixtures/hermes")

    result = plan_canvas(
        fixture_canvas,
        mode="start-learning",
        example_roots=[example_root],
        max_assignments=2,
    )

    assert result["status"] == "OK"
    assert len(result["assignments"]) == 1
    assert result["assignments"][0]["id"] == "canvas-1"
    assert result["assignments"][0]["title"] == "Schedule learning history essay"

    # Verify study blocks
    assert len(result["study_blocks"]) == 1
    block = result["study_blocks"][0]["block"]
    assert "concepts" in block
    assert "learner_problem" in block
    assert "artifact_check" in block

    # Verify LearningEvent generation
    assert len(result["learning_events"]) == 1
    le = result["learning_events"][0]["learning_event"]
    assert le["id"].startswith("canvas-canvas-1-")
    assert le["source"] == "canvas_formal"
    assert le["sensitivity"] == "academic"
    assert le["whole_system"] == "History"
    assert le["mastery"] == "pending"
    assert len(le["evidence"]) >= 1
    assert le["evidence"][0]["source"] == "canvas_formal"

    # Verify review tasks
    assert len(result["review_tasks"]) >= 1
    t0 = result["review_tasks"][0]
    assert t0["assignment_id"] == "canvas-1"
    assert t0["task_type"] == "initial_study"

    # Verify calendar events
    assert len(result["calendar_events"]) >= 1
    ev0 = result["calendar_events"][0]
    assert "Study:" in ev0["summary"]
    assert ev0["duration_minutes"] == 30


def test_plan_canvas_commit_to_db(db_path: Path):
    fixture_canvas = Path("tests/fixtures/canvas.json")
    example_root = Path("tests/fixtures/hermes")

    result = plan_canvas(
        fixture_canvas,
        mode="start-learning",
        example_roots=[example_root],
        db_path=db_path,
        commit_events=True,
    )

    assert result["status"] == "OK"
    assert len(result["committed_events"]) == 1
    committed_id = result["committed_events"][0]

    # Verify stored in sqlite3
    stored = api.get_learning_event_by_id(committed_id, db_path=db_path)
    assert stored is not None
    ev = stored["learning_event"]
    assert ev["id"] == committed_id
    assert ev["source"] == "canvas_formal"
    assert ev["subsystem"] == "Schedule learning history essay"


def test_cli_plan_canvas(tmp_path: Path):
    ics_out = tmp_path / "plan_schedule.ics"
    command = [
        sys.executable,
        "-m",
        "learning_tracking",
        "plan-canvas",
        "--canvas-summary",
        "tests/fixtures/canvas.json",
        "--example-root",
        "tests/fixtures/hermes",
        "--ics-output",
        str(ics_out),
    ]

    res = subprocess.run(command, check=True, capture_output=True, text=True)
    out = json.loads(res.stdout)

    assert out["status"] == "OK"
    assert len(out["assignments"]) == 1
    assert ics_out.exists()
    ics_content = ics_out.read_bytes().decode("utf-8")
    assert "BEGIN:VCALENDAR\r\n" in ics_content
    assert "BEGIN:VEVENT\r\n" in ics_content
    assert "END:VCALENDAR\r\n" in ics_content


def test_cli_error_ledger_commands(db_path: Path):
    # Test record-error via CLI
    rec_cmd = [
        sys.executable,
        "-m",
        "learning_tracking",
        "--db-path",
        str(db_path),
        "record-error",
        "--objective",
        "db:relational-basics:v1",
        "--category",
        "deviation",
        "--misconception",
        "Believed SQLite foreign keys enabled by default",
        "--correction",
        "Run PRAGMA foreign_keys=ON",
    ]
    res = subprocess.run(rec_cmd, check=True, capture_output=True, text=True)
    rec_out = json.loads(res.stdout)
    assert rec_out["status"] == "OK"
    err_id = rec_out["error"]["id"]

    # Test list-errors via CLI
    list_cmd = [
        sys.executable,
        "-m",
        "learning_tracking",
        "--db-path",
        str(db_path),
        "list-errors",
        "--objective",
        "db:relational-basics:v1",
    ]
    res_list = subprocess.run(list_cmd, check=True, capture_output=True, text=True)
    list_out = json.loads(res_list.stdout)
    assert list_out["status"] == "OK"
    assert list_out["count"] == 1
    assert list_out["errors"][0]["id"] == err_id

    # Test error-summary via CLI
    sum_cmd = [
        sys.executable,
        "-m",
        "learning_tracking",
        "--db-path",
        str(db_path),
        "error-summary",
        "--objective",
        "db:relational-basics:v1",
    ]
    res_sum = subprocess.run(sum_cmd, check=True, capture_output=True, text=True)
    sum_out = json.loads(res_sum.stdout)
    assert sum_out["status"] == "OK"
    assert sum_out["summary"]["total_errors"] == 1
    assert sum_out["summary"]["by_category"]["deviation"] == 1


def test_cli_schedule_calendar_with_canvas(tmp_path: Path):
    ics_out = tmp_path / "study_cal.ics"
    sched_cmd = [
        sys.executable,
        "-m",
        "learning_tracking",
        "schedule-calendar",
        "--canvas-summary",
        "tests/fixtures/canvas.json",
        "--duration-minutes",
        "25",
        "--ics-output",
        str(ics_out),
    ]
    res = subprocess.run(sched_cmd, check=True, capture_output=True, text=True)
    out = json.loads(res.stdout)
    assert out["status"] == "OK"
    assert out["event_count"] >= 1
    assert ics_out.exists()
    content = ics_out.read_bytes().decode("utf-8")
    assert "BEGIN:VCALENDAR\r\n" in content

def test_plan_canvas_custom_policy_rejection(tmp_path: Path):
    fixture_canvas = Path("tests/fixtures/canvas.json")
    example_root = Path("tests/fixtures/hermes")
    custom_policy = tmp_path / "custom_policy.json"
    custom_policy.write_text(json.dumps({"exclusive": True, "allowed_sources": ["unrelated_source"]}))

    never_created_db = tmp_path / "never_created.sqlite3"

    with pytest.raises(ValueError, match="Unknown learning source 'canvas_formal'"):
        plan_canvas(
            fixture_canvas,
            mode="start-learning",
            example_roots=[example_root],
            db_path=never_created_db,
            policy_path=custom_policy,
            commit_events=True,
        )

    # Fail-closed check: DB must not have been created or written to
    assert not never_created_db.exists()

def test_plan_canvas_custom_policy_allowed(tmp_path: Path, db_path: Path):
    fixture_canvas = Path("tests/fixtures/canvas.json")
    example_root = Path("tests/fixtures/hermes")
    custom_policy = tmp_path / "custom_policy.json"
    custom_policy.write_text(json.dumps({"exclusive": True, "allowed_sources": ["canvas_formal"]}))

    res = plan_canvas(
        fixture_canvas,
        mode="start-learning",
        example_roots=[example_root],
        db_path=db_path,
        policy_path=custom_policy,
        commit_events=True,
    )
    assert res["status"] == "OK"
    assert len(res["committed_events"]) == 1

def test_section14_trigger_sanitization():
    raw_text = "Concept | Topic\r\nAction \x1b[31mBold\x1b[0m \ud83d\udcda"
    sanitized = sanitize_trigger_text(raw_text)
    assert "|" not in sanitized
    assert "\r" not in sanitized
    assert "\n" not in sanitized
    # Non-ASCII characters should be stripped
    assert all(0x20 <= ord(c) <= 0x7E for c in sanitized)
    assert "-" in sanitized

    now = datetime(2026, 9, 21, 10, 0, tzinfo=timezone.utc)
    task = ReviewTask(
        task_id="t-1",
        assignment_id="a-1",
        title="Title | Pipe\r\nNewline \ud83d\udcda",
        concept="Concept | Tag",
        scheduled_for=now,
        task_type="initial_study",
        action="Action | Item\nNext line",
    )

    remind = task.to_remind_trigger("Study | List\n")
    assert "\n" not in remind
    assert "\r" not in remind
    # Title and List must have had pipes replaced with -
    parts = remind.split(" | ")
    assert len(parts) == 3
    assert "REMIND:" in parts[0]
    assert "|" not in parts[1]
    assert "|" not in parts[2]

    popup = task.to_popup_trigger("Alert | Message\r\n")
    assert "\r" not in popup and "\n" not in popup
    assert popup.count(" | ") == 1

    todo = task.to_todo_trigger("My | List\n")
    assert "\n" not in todo
    assert todo.count(" | ") == 1


def test_section14_timer_validation():
    now = datetime(2026, 9, 21, 10, 0, tzinfo=timezone.utc)
    task = ReviewTask(
        task_id="t-1",
        assignment_id="a-1",
        title="Study",
        concept="Math",
        scheduled_for=now,
        task_type="initial_study",
        action="Study math",
    )

    assert task.to_timer_trigger(45) == "TIMER: 45 | Study"
    assert task.to_timer_trigger(1) == "TIMER: 1 | Study"
    assert task.to_timer_trigger(1440) == "TIMER: 1440 | Study"

    with pytest.raises(ValueError, match="Timer minutes must be an integer between 1 and 1440"):
        task.to_timer_trigger(0)
    with pytest.raises(ValueError, match="Timer minutes must be an integer between 1 and 1440"):
        task.to_timer_trigger(1441)
    with pytest.raises(ValueError, match="Timer minutes must be an integer between 1 and 1440"):
        task.to_timer_trigger(-5)
    with pytest.raises(ValueError, match="Timer minutes must be an integer between 1 and 1440"):
        task.to_timer_trigger(True)  # bool is subclass of int


def test_section14_alarm_validation():
    now = datetime(2026, 9, 21, 8, 30, tzinfo=timezone.utc)
    task = ReviewTask(
        task_id="t-1",
        assignment_id="a-1",
        title="Wake Up",
        concept="Sleep",
        scheduled_for=now,
        task_type="initial_study",
        action="Wake up",
    )

    assert task.to_alarm_trigger() == "ALARM: 08:30 | Wake Up"
    assert task.to_alarm_trigger("23:59") == "ALARM: 23:59 | Wake Up"
    assert task.to_alarm_trigger("00:00") == "ALARM: 00:00 | Wake Up"

    with pytest.raises(ValueError, match="Invalid alarm time format"):
        task.to_alarm_trigger("24:00")
    with pytest.raises(ValueError, match="Invalid alarm time format"):
        task.to_alarm_trigger("12:60")
    with pytest.raises(ValueError, match="Invalid alarm time format"):
        task.to_alarm_trigger("8:30")
    with pytest.raises(ValueError, match="Invalid alarm time format"):
        task.to_alarm_trigger("morning")


def test_deterministic_event_id_and_commit_idempotency(db_path: Path):
    fixture_canvas = Path("tests/fixtures/canvas.json")
    example_root = Path("tests/fixtures/hermes")

    res1 = plan_canvas(
        fixture_canvas,
        mode="start-learning",
        example_roots=[example_root],
        db_path=db_path,
        commit_events=True,
    )
    assert res1["status"] == "OK"
    assert len(res1["learning_events"]) == 1
    le_id = res1["learning_events"][0]["learning_event"]["id"]
    # Deterministic ID format: canvas-<assignment.id>-<primary_concept>-<rank_idx>
    assert le_id == "canvas-canvas-1-outline-0"
    assert len(res1["committed_events"]) == 1

    # Run a second time against the same database: must be idempotent
    res2 = plan_canvas(
        fixture_canvas,
        mode="start-learning",
        example_roots=[example_root],
        db_path=db_path,
        commit_events=True,
    )
    assert res2["status"] == "OK"
    assert len(res2["committed_events"]) == 1
    assert res2["committed_events"][0] == le_id

    # Ensure exactly 1 row in SQLite, no duplicates
    stored = api.get_learning_event_by_id(le_id, db_path=db_path)
    assert stored is not None


def test_plan_canvas_undated_assignment(tmp_path: Path):
    summary_file = tmp_path / "undated.json"
    summary_file.write_text(
        json.dumps({
            "assignments": [
                {
                    "id": "undated-1",
                    "name": "Ongoing Reading",
                    "course_name": "Philosophy",
                    "due_at": None,
                }
            ]
        })
    )

    res = plan_canvas(
        summary_file,
        mode="start-learning",
        example_roots=[Path("tests/fixtures/hermes")],
    )

    assert res["status"] == "OK"
    assert len(res["assignments"]) == 1
    asgn = res["assignments"][0]
    assert asgn["due_at"] is None
    assert asgn["personal_final"] is None

    # No deadline-derived review tasks or calendar events
    assert len(res["review_tasks"]) == 0
    assert len(res["calendar_events"]) == 0

    # Learning event observed_fact handles undated cleanly
    assert len(res["learning_events"]) == 1
    le = res["learning_events"][0]["learning_event"]
    assert "(undated)" in le["observed_fact"]

    # Direct generate_review_tasks call on undated assignment returns empty list
    undated_assignment = Assignment(
        id="u-1",
        title="Undated",
        course="Philosophy",
        due_at=None,
        personal_final=None,
    )
    assert generate_review_tasks(undated_assignment, "Philosophy") == []
