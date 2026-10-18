"""Tests for Google/iCloud calendar review scheduling adapter and RFC 5545 exporter."""

from __future__ import annotations

from datetime import date, datetime, time, timezone
from pathlib import Path
import pytest

from learning_tracking.calendar_schedule import (
    CalendarEvent,
    CalendarProviderError,
    CalendarProviderUnconfiguredError,
    CalDAVCalendarProvider,
    CalDavProvider,
    GoogleCalendarProvider,
    StudyWindow,
    export_icalendar,
    sanitize_calendar_text,
    schedule_study_sessions,
    write_icalendar,
)


def test_sanitize_calendar_text_strips_emails_secrets_and_private_paths():
    leaky_text = (
        "Study session for student user@example.edu with auth Bearer secret_token_12345 "
        "and ghp_abc123token and file at /home/phroggingphrog/.ssh/id_rsa on topic SQL."
    )
    clean = sanitize_calendar_text(leaky_text)

    assert "user@example.edu" not in clean
    assert "[REDACTED_EMAIL]" in clean
    assert "secret_token_12345" not in clean
    assert "[REDACTED_SECRET]" in clean
    assert "ghp_abc123token" not in clean
    assert "/home/phroggingphrog/.ssh" not in clean
    assert "[REDACTED_PATH]" in clean
    assert "SQL" in clean


def test_schedule_study_sessions_respects_study_window():
    items = [
        {"title": "Review 1", "description": "Topic 1"},
        {"title": "Review 2", "description": "Topic 2"},
        {"title": "Review 3", "description": "Topic 3"},
        {"title": "Review 4", "description": "Topic 4"},
        {"title": "Review 5", "description": "Topic 5"},
    ]
    window = StudyWindow(
        start_hour=18,
        start_minute=0,
        end_hour=20,
        end_minute=0,
        session_duration_minutes=30,
        break_minutes=5,
        max_sessions_per_day=3,
    )
    start = datetime(2026, 9, 22, 10, 0, tzinfo=timezone.utc)
    events = schedule_study_sessions(items, start_date=start, study_window=window)

    assert len(events) == 5

    # First 3 sessions scheduled on day 1 (max_sessions_per_day=3)
    ev0, ev1, ev2 = events[0], events[1], events[2]
    assert ev0.start_time.date() == date(2026, 9, 22)
    assert ev0.start_time.hour == 18 and ev0.start_time.minute == 0
    assert ev0.end_time.hour == 18 and ev0.end_time.minute == 30

    assert ev1.start_time.date() == date(2026, 9, 22)
    assert ev1.start_time.hour == 18 and ev1.start_time.minute == 35
    assert ev1.end_time.hour == 19 and ev1.end_time.minute == 5

    assert ev2.start_time.date() == date(2026, 9, 22)
    assert ev2.start_time.hour == 19 and ev2.start_time.minute == 10
    assert ev2.end_time.hour == 19 and ev2.end_time.minute == 40

    # 4th and 5th sessions push to day 2
    ev3, ev4 = events[3], events[4]
    assert ev3.start_time.date() == date(2026, 9, 23)
    assert ev3.start_time.hour == 18 and ev3.start_time.minute == 0
    assert ev4.start_time.date() == date(2026, 9, 23)
    assert ev4.start_time.hour == 18 and ev4.start_time.minute == 35


def test_export_icalendar_rfc5545_compliance(tmp_path: Path):
    ev = CalendarEvent(
        uid="review-12345@learning-tracking.hermes",
        summary="Study: Relational Database Schema Design",
        description="Verify normalization 3NF constraints and functional dependencies.",
        start_time=datetime(2026, 9, 22, 18, 0, tzinfo=timezone.utc),
        end_time=datetime(2026, 9, 22, 18, 30, tzinfo=timezone.utc),
        categories=("STUDY", "ACADEMIA"),
    )

    ics_content = export_icalendar([ev])

    assert "BEGIN:VCALENDAR\r\n" in ics_content
    assert "VERSION:2.0\r\n" in ics_content
    assert "PRODID:-//Hermes Life OS//Learning Tracking Review Scheduler//EN\r\n" in ics_content
    assert "BEGIN:VEVENT\r\n" in ics_content
    assert "UID:review-12345@learning-tracking.hermes\r\n" in ics_content
    assert "DTSTART:20260922T180000Z\r\n" in ics_content
    assert "DTEND:20260922T183000Z\r\n" in ics_content
    assert "SUMMARY:Study: Relational Database Schema Design\r\n" in ics_content
    assert "STATUS:CONFIRMED\r\n" in ics_content
    assert "END:VEVENT\r\n" in ics_content
    assert "END:VCALENDAR\r\n" in ics_content

    # Write to file
    out_file = tmp_path / "study_schedule.ics"
    write_icalendar([ev], out_file)
    assert out_file.exists()
    assert out_file.read_bytes().decode("utf-8") == ics_content


def test_google_and_apple_payloads():
    ev = CalendarEvent(
        uid="study-abc@learning-tracking.hermes",
        summary="Bayesian Priors Review",
        description="Review conjugate priors and MCMC posterior diagnostics.",
        start_time=datetime(2026, 9, 22, 18, 0, tzinfo=timezone.utc),
        end_time=datetime(2026, 9, 22, 18, 30, tzinfo=timezone.utc),
    )

    g_payload = ev.to_google_payload()
    assert g_payload["summary"] == "Bayesian Priors Review"
    assert g_payload["start"]["dateTime"] == "2026-09-22T18:00:00+00:00"
    assert g_payload["end"]["dateTime"] == "2026-09-22T18:30:00+00:00"
    assert g_payload["status"] == "confirmed"

    a_payload = ev.to_apple_payload()
    assert a_payload["title"] == "Bayesian Priors Review"
    assert a_payload["notes"] == "Review conjugate priors and MCMC posterior diagnostics."
    assert a_payload["startDate"] == "2026-09-22T18:00:00+00:00"
    assert a_payload["endDate"] == "2026-09-22T18:30:00+00:00"


def test_read_events_sanitized_error_propagation_and_404(monkeypatch: pytest.MonkeyPatch):
    import urllib.request
    import urllib.error

    # Google read error propagates sanitized CalendarProviderError
    def mock_urlopen_google_err(req, **kwargs):
        raise urllib.error.HTTPError(
            req.full_url,
            403,
            "Forbidden: Bearer ghp_secrettoken123 on /home/phroggingphrog/.hermes/cfg",
            {},
            None,
        )

    monkeypatch.setattr(urllib.request, "urlopen", mock_urlopen_google_err)
    g_prov = GoogleCalendarProvider(config={"access_token": "ghp_secrettoken123"})
    with pytest.raises(CalendarProviderError) as exc_info:
        g_prov.read_events()
    err = str(exc_info.value)
    assert "ghp_secrettoken123" not in err
    assert "/home/phroggingphrog/.hermes" not in err
    assert "[REDACTED_SECRET]" in err

    # Google read 404 returns []
    def mock_urlopen_google_404(req, **kwargs):
        raise urllib.error.HTTPError(req.full_url, 404, "Calendar not found", {}, None)

    monkeypatch.setattr(urllib.request, "urlopen", mock_urlopen_google_404)
    assert g_prov.read_events() == []

    # CalDAV read error propagates sanitized CalendarProviderError
    def mock_urlopen_caldav_err(req, **kwargs):
        raise urllib.error.HTTPError(
            req.full_url,
            500,
            "Internal Server Error: password=mypassword on /home/phroggingphrog/.config/gcloud",
            {},
            None,
        )

    monkeypatch.setattr(urllib.request, "urlopen", mock_urlopen_caldav_err)
    c_prov = CalDavProvider(config={
        "CALDAV_URL": "https://caldav.example.com/dav",
        "CALDAV_PASSWORD": "mypassword",
    })
    with pytest.raises(CalendarProviderError) as exc_info_c:
        c_prov.read_events()
    err_c = str(exc_info_c.value)
    assert "mypassword" not in err_c
    assert "[REDACTED_SECRET]" in err_c

    # CalDAV read 404 returns []
    def mock_urlopen_caldav_404(req, **kwargs):
        raise urllib.error.HTTPError(req.full_url, 404, "Calendar not found", {}, None)

    monkeypatch.setattr(urllib.request, "urlopen", mock_urlopen_caldav_404)
    assert c_prov.read_events() == []


def test_sync_events_http_409_idempotent_reconciliation(monkeypatch: pytest.MonkeyPatch):
    import urllib.request
    import urllib.error
    import json
    from unittest.mock import MagicMock

    ev = CalendarEvent(
        uid="conflict-study-event-409@hermes.learning",
        summary="Concurrency Conflict Review",
        description="Testing idempotent recovery from 409 Conflict",
        start_time=datetime(2026, 9, 22, 18, 0, tzinfo=timezone.utc),
        end_time=datetime(2026, 9, 22, 18, 30, tzinfo=timezone.utc),
    )
    expected_google_id = ev.to_google_payload()["id"]

    # Google 409 conflict: POST returns 409, GET reconciles idempotently
    google_requests = []

    def mock_urlopen_google(req, **kwargs):
        google_requests.append((req.get_method(), req.full_url))
        if req.get_method() == "POST":
            raise urllib.error.HTTPError(req.full_url, 409, "Conflict: event exists", {}, None)
        elif req.get_method() == "GET":
            resp = MagicMock()
            resp.read.return_value = json.dumps({"id": expected_google_id, "status": "confirmed"}).encode("utf-8")
            resp.__enter__.return_value = resp
            return resp
        raise RuntimeError(f"Unexpected method {req.get_method()}")

    monkeypatch.setattr(urllib.request, "urlopen", mock_urlopen_google)
    g_prov = GoogleCalendarProvider(config={"access_token": "token_123"})
    res = g_prov.sync_events([ev])
    assert res["status"] == "success"
    assert res["synced_count"] == 1
    assert res["events"][0]["google_id"] == expected_google_id
    assert [r[0] for r in google_requests] == ["POST", "GET"]

    # CalDAV 409 conflict: PUT returns 409, GET reconciles idempotently
    caldav_requests = []

    def mock_urlopen_caldav(req, **kwargs):
        caldav_requests.append((req.get_method(), req.full_url))
        if req.get_method() == "PUT":
            raise urllib.error.HTTPError(req.full_url, 409, "Conflict", {}, None)
        elif req.get_method() == "GET":
            resp = MagicMock()
            resp.status = 200
            resp.read.return_value = f"BEGIN:VCALENDAR\r\nUID:{ev.uid}\r\nEND:VCALENDAR".encode("utf-8")
            resp.__enter__.return_value = resp
            return resp
        raise RuntimeError(f"Unexpected method {req.get_method()}")

    monkeypatch.setattr(urllib.request, "urlopen", mock_urlopen_caldav)
    c_prov = CalDAVCalendarProvider(config={"CALDAV_URL": "https://caldav.example.com/dav"})
    res_c = c_prov.sync_events([ev])
    assert res_c["status"] == "success"
    assert res_c["synced_count"] == 1
    assert res_c["events"][0]["uid"] == ev.uid
    assert [r[0] for r in caldav_requests] == ["PUT", "GET"]


def test_sync_events_remote_id_verification(monkeypatch: pytest.MonkeyPatch):
    import urllib.request
    import json
    from unittest.mock import MagicMock

    ev = CalendarEvent(
        uid="verify-study-event@hermes.learning",
        summary="Remote ID Verification",
        description="Verify event ID matches",
        start_time=datetime(2026, 9, 22, 18, 0, tzinfo=timezone.utc),
        end_time=datetime(2026, 9, 22, 18, 30, tzinfo=timezone.utc),
    )
    expected_google_id = ev.to_google_payload()["id"]

    # Case 1: Remote ID confirmed via response payload
    def mock_confirm(req, **kwargs):
        resp = MagicMock()
        resp.read.return_value = json.dumps({"id": expected_google_id, "status": "confirmed"}).encode("utf-8")
        resp.__enter__.return_value = resp
        return resp

    monkeypatch.setattr(urllib.request, "urlopen", mock_confirm)
    g_prov = GoogleCalendarProvider(config={"access_token": "token_123"})
    res = g_prov.sync_events([ev])
    assert res["status"] == "success"
    assert res["events"][0]["google_id"] == expected_google_id

    # Case 2: Remote ID verified via GET fallback when response lacks ID
    methods = []

    def mock_get_verify(req, **kwargs):
        methods.append(req.get_method())
        resp = MagicMock()
        if req.get_method() == "POST":
            resp.read.return_value = json.dumps({"status": "confirmed"}).encode("utf-8")
        elif req.get_method() == "GET":
            resp.read.return_value = json.dumps({"id": expected_google_id, "status": "confirmed"}).encode("utf-8")
        resp.__enter__.return_value = resp
        return resp

    monkeypatch.setattr(urllib.request, "urlopen", mock_get_verify)
    res = g_prov.sync_events([ev])
    assert res["status"] == "success"
    assert res["events"][0]["google_id"] == expected_google_id
    assert methods == ["POST", "GET"]

    # Case 3: Verification failure when remote ID does not match
    def mock_mismatch(req, **kwargs):
        resp = MagicMock()
        resp.read.return_value = json.dumps({"id": "wrong-id", "status": "confirmed"}).encode("utf-8")
        resp.__enter__.return_value = resp
        return resp

    monkeypatch.setattr(urllib.request, "urlopen", mock_mismatch)
    res = g_prov.sync_events([ev])
    assert res["status"] == "error"
    assert "verification failed" in res["error"].lower()
