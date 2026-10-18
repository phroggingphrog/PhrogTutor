from datetime import date, datetime, timezone
import json
from pathlib import Path

from learning_tracking.canvas import load_assignments


def test_canvas_due_date_gets_fourteen_day_personal_deadline():
    assignment = load_assignments(Path("tests/fixtures/canvas.json"))[0]

    assert assignment.due_at.date() == date(2026, 9, 23)
    assert assignment.original_due_at.date() == date(2026, 9, 9)
    assert assignment.personal_final.date() == date(2026, 9, 9)
    assert assignment.extended_due_at.date() == date(2026, 9, 23)
    assert assignment.protection_start.date() == date(2026, 9, 2)


def test_canvas_upcoming_summary_maps_live_assignment_fields():
    assignment = load_assignments(Path("tests/fixtures/canvas_upcoming.json"))[0]

    assert assignment.id == "987654"
    assert assignment.title == "Hermes integration reflection"
    assert assignment.course == "Software Engineering"
    assert assignment.due_at.date() == date(2026, 9, 24)
    assert assignment.original_due_at.date() == date(2026, 9, 10)
    assert assignment.personal_final.date() == date(2026, 9, 10)
    assert assignment.extended_due_at.date() == date(2026, 9, 24)
    assert assignment.protection_start.date() == date(2026, 9, 3)


def test_known_earlier_original_deadline_wins(tmp_path):
    path = tmp_path / "canvas.json"
    path.write_text(
        json.dumps(
            {
                "upcoming": [
                    {
                        "id": 1,
                        "name": "Assessment",
                        "course_name": "Database Concepts",
                        "due_at": "2026-10-01T12:00:00Z",
                        "original_due_at": "2026-09-15T12:00:00Z",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    assignment = load_assignments(path)[0]

    assert assignment.personal_final == datetime(2026, 9, 15, 12, tzinfo=timezone.utc)
    assert assignment.protection_start == datetime(2026, 9, 8, 12, tzinfo=timezone.utc)
