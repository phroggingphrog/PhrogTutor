"""Tests for Canvas API configuration, HTTPS enforcement, pagination, and undated assignments."""

from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
from unittest.mock import MagicMock, patch
import urllib.error
import pytest

from learning_tracking.canvas import fetch_assignments_from_api, load_assignments
from learning_tracking.models import Assignment
from learning_tracking.planner import rank_assignments


def test_fetch_assignments_enforces_https():
    with pytest.raises(ValueError, match="Canvas base URL must use HTTPS"):
        fetch_assignments_from_api(
            base_url="http://insecure-canvas.example.com",
            token="dummy-token",
            course_ids=[1],
        )


def test_fetch_assignments_rejects_cross_origin_pagination():
    mock_resp = MagicMock()
    mock_resp.read.return_value = json.dumps([{"id": 101, "name": "Task 1"}]).encode("utf-8")
    mock_resp.headers = {
        "Link": '<https://attacker-controlled.com/api/v1/courses/1/assignments?page=2>; rel="next"'
    }

    with patch("urllib.request.urlopen") as mock_urlopen:
        mock_urlopen.return_value.__enter__.return_value = mock_resp
        with pytest.raises(ValueError, match="Cross-origin or insecure pagination URL rejected"):
            fetch_assignments_from_api(
                base_url="https://canvas.institution.edu",
                token="dummy-token",
                course_ids=[1],
            )


def test_fetch_assignments_rejects_insecure_http_pagination():
    mock_resp = MagicMock()
    mock_resp.read.return_value = json.dumps([{"id": 101, "name": "Task 1"}]).encode("utf-8")
    mock_resp.headers = {
        "Link": '<http://canvas.institution.edu/api/v1/courses/1/assignments?page=2>; rel="next"'
    }

    with patch("urllib.request.urlopen") as mock_urlopen:
        mock_urlopen.return_value.__enter__.return_value = mock_resp
        with pytest.raises(ValueError, match="Cross-origin or insecure pagination URL rejected"):
            fetch_assignments_from_api(
                base_url="https://canvas.institution.edu",
                token="dummy-token",
                course_ids=[1],
            )


def test_fetch_assignments_undated_never_invents_deadline():
    mock_resp = MagicMock()
    mock_resp.read.return_value = json.dumps([
        {"id": 201, "name": "Undated Lab", "due_at": None},
        {"id": 202, "name": "Dated Lab", "due_at": "2026-10-15T23:59:00Z"},
    ]).encode("utf-8")
    mock_resp.headers = {}

    with patch("urllib.request.urlopen") as mock_urlopen:
        mock_urlopen.return_value.__enter__.return_value = mock_resp
        assignments = fetch_assignments_from_api(
            base_url="https://canvas.institution.edu",
            token="dummy-token",
            course_ids=[1],
        )

    assert len(assignments) == 2
    undated = assignments[0]
    assert undated.id == "201"
    assert undated.due_at is None
    assert undated.personal_final is None

    dated = assignments[1]
    assert dated.id == "202"
    assert dated.due_at is not None
    assert dated.personal_final is not None


def test_load_assignments_undated_json(tmp_path: Path):
    summary_file = tmp_path / "summary.json"
    summary_file.write_text(
        json.dumps({
            "assignments": [
                {"id": "a-1", "name": "Undated Reading", "due_at": None, "course_name": "CS101"},
                {"id": "a-2", "name": "Dated Project", "due_at": "2026-11-01T12:00:00+00:00", "course_name": "CS101"},
            ]
        })
    )

    loaded = load_assignments(summary_file)
    assert len(loaded) == 2
    assert loaded[0].due_at is None
    assert loaded[0].personal_final is None
    assert loaded[1].due_at == datetime(2026, 11, 1, 12, 0, tzinfo=timezone.utc)


def test_rank_assignments_sorts_undated_after_dated():
    dated_early = Assignment(
        id="dated-1",
        title="Early Assignment",
        course="CS101",
        due_at=datetime(2026, 9, 20, tzinfo=timezone.utc),
        personal_final=datetime(2026, 9, 6, tzinfo=timezone.utc),
        original_due_at=datetime(2026, 9, 6, tzinfo=timezone.utc),
        extended_due_at=datetime(2026, 9, 20, tzinfo=timezone.utc),
    )
    dated_late = Assignment(
        id="dated-2",
        title="Late Assignment",
        course="CS101",
        due_at=datetime(2026, 10, 1, tzinfo=timezone.utc),
        personal_final=datetime(2026, 9, 17, tzinfo=timezone.utc),
        original_due_at=datetime(2026, 9, 17, tzinfo=timezone.utc),
        extended_due_at=datetime(2026, 10, 1, tzinfo=timezone.utc),
    )
    undated_a = Assignment(
        id="undated-a",
        title="Undated A",
        course="CS101",
        due_at=None,
        personal_final=None,
    )
    undated_b = Assignment(
        id="undated-b",
        title="Undated B",
        course="CS101",
        due_at=None,
        personal_final=None,
    )

    # Scramble order
    ranked = rank_assignments([undated_b, dated_late, undated_a, dated_early])
    # Dated items come first in order of deadline; undated items follow sorted by tiebreak (id)
    assert [item.id for item in ranked] == ["dated-1", "dated-2", "undated-a", "undated-b"]
