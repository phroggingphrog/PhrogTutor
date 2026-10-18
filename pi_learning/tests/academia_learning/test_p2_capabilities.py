"""Comprehensive tests for P2 capabilities:
1. Calendar integration (Google / CalDAV / iCloud status, clean provider interfaces, .ics fallback).
2. Automatic case-study generation from verified incidents (sanitization, top-down structure).
3. Spaced repetition & active-recall quizzes (FSRS integration, error ledger remediation).
4. Learning dashboard & progress analytics (BKT mastery, FSRS queues, error hotspots).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import re
import sqlite3
import pytest

from learning_tracking.academia_learning import api
from learning_tracking.academia_learning.case_study import (
    CaseStudyModule,
    UnverifiedEventError,
    generate_case_studies,
    generate_case_study_from_event,
    sanitize_case_study_text,
)
from learning_tracking.academia_learning.dashboard import (
    DashboardReport,
    generate_dashboard_report,
)
from learning_tracking.academia_learning.db import (
    connect,
    ensure_error_ledger_schema,
    ensure_learning_event_schema,
    ensure_review_event_schema,
)
from learning_tracking.academia_learning.error_ledger import record_error
from learning_tracking.academia_learning.learning_event import (
    LearningEvent,
    insert_learning_event,
)
from learning_tracking.academia_learning.quiz import (
    QuizQuestion,
    generate_quiz_questions,
    grade_quiz_answer,
    resolve_quiz_rubric,
    run_quiz_session,
    submit_quiz_answer,
)
from learning_tracking.calendar_schedule import (
    CalendarEvent,
    CalendarProviderError,
    CalendarProviderStatus,
    CalendarProviderUnconfiguredError,
    CalendarSyncManager,
    CalDAVCalendarProvider,
    GoogleCalendarProvider,
    get_calendar_provider_status,
)


@pytest.fixture
def populated_db(db_path: Path) -> Path:
    """Fixture DB with seeded learning events and error ledger entries."""
    ensure_learning_event_schema(db_path)
    ensure_error_ledger_schema(db_path)
    ensure_review_event_schema(db_path)

    db = connect(db_path, write=True)

    # Seed verified learning event
    event = LearningEvent(
        id="incident-cron-001",
        source="hermes_runtime",
        sensitivity="operational",
        whole_system="Hermes",
        subsystem="Scheduler",
        concept="Cron Idempotency",
        concept_tags=("cron", "concurrency", "lock"),
        observed_fact="Two cron workers overlapped due to missing advisory lock.",
        evidence=({"source": "sanitized_service_log", "reference": "log-001"},),
        lesson={
            "concept": "Advisory locks guarantee mutual exclusion.",
            "explanation": "Cron jobs touching shared state must acquire an advisory lock.",
        },
        exercise={
            "task": "Acquire SQLite immediate transaction or flock before proceeding.",
        },
        verification={
            "command": "python3 -c 'import fcntl; print(\"OK\")'",
        },
        verified_result="Exit code 0 with OK printed",
        sanitized_case_summary=True,
    )
    insert_learning_event(db, event)

    # Seed an error ledger entry
    record_error(
        db,
        objective_version_id="db:relational-basics:v1",
        error_category="logic",
        misconception="Thought UNIQUE index allows duplicate NULLs arbitrarily",
        failed_step="Applying unique constraint check",
        correction="Verify nullability semantics in SQL standard.",
    )

    db.commit()
    db.close()

    # Seed successful attempts for BKT mastery testing
    for i in range(5):
        api.append_attempt(
            objective_version_id="db:relational-basics:v1",
            response_ref=f"ref:seed-{i}",
            assistance_level="user-provided",
            confidence_before=None,
            confidence_after=3,
            next_action="n/a",
            db_path=db_path,
        )

    return db_path


# ==============================================================================
# 1. Calendar Integration & Provider Status Tests
# ==============================================================================

def test_calendar_status_reports_unconfigured_without_secrets(tmp_path: Path):
    empty_hermes = tmp_path / "empty_hermes"
    empty_hermes.mkdir()

    status = get_calendar_provider_status(hermes_dir=empty_hermes)
    assert isinstance(status, CalendarProviderStatus)
    # Ensure no secrets or tokens are in status dict
    status_dict = status.to_dict()
    assert "google_available" in status_dict
    assert "caldav_available" in status_dict
    assert "actionable_message" in status_dict
    assert not status.google_available
    assert not status.caldav_available
    assert "No calendar API credentials configured" in status.actionable_message


def test_calendar_providers_fail_cleanly_when_unconfigured():
    status = CalendarProviderStatus(
        google_available=False,
        google_source=None,
        caldav_available=False,
        caldav_source=None,
        actionable_message="Unconfigured for testing",
    )
    g_prov = GoogleCalendarProvider(status=status)
    c_prov = CalDAVCalendarProvider(status=status)

    assert not g_prov.is_configured()
    assert not c_prov.is_configured()

    from datetime import datetime, timezone, timedelta
    now_dt = datetime.now(timezone.utc)
    dummy_event = CalendarEvent(
        uid="test-uid-1",
        summary="Test Study Block",
        description="Reviewing SQL",
        start_time=now_dt,
        end_time=now_dt + timedelta(minutes=30),
    )

    # Calling sync on unconfigured provider returns unconfigured status
    res_g = g_prov.sync_events([dummy_event])
    assert res_g["configured"] is False
    assert res_g["status"] == "unconfigured"

    res_c = c_prov.sync_events([dummy_event])
    assert res_c["configured"] is False
    assert res_c["status"] == "unconfigured"

    # Calling with raise_if_unconfigured=True raises CalendarProviderUnconfiguredError
    with pytest.raises(CalendarProviderUnconfiguredError):
        g_prov.sync_events([dummy_event], raise_if_unconfigured=True)

    with pytest.raises(CalendarProviderUnconfiguredError):
        c_prov.sync_events([dummy_event], raise_if_unconfigured=True)


def test_calendar_sync_manager_falls_back_to_ics(tmp_path: Path):
    ics_file = tmp_path / "reviews.ics"
    manager = CalendarSyncManager(hermes_dir=tmp_path)

    from datetime import datetime, timezone, timedelta
    now_dt = datetime.now(timezone.utc)
    ev = CalendarEvent(
        uid="study-block-42",
        summary="Systems Study Session",
        description="FSRS Review Block",
        start_time=now_dt,
        end_time=now_dt + timedelta(minutes=30),
    )

    sync_res = manager.sync([ev], ics_output_path=ics_file)

    assert sync_res["status"] == "FALLBACK_ICS"
    assert sync_res["ics_path"] == str(ics_file)
    assert ics_file.exists()

    ics_content = ics_file.read_text(encoding="utf-8")
    assert "BEGIN:VCALENDAR" in ics_content
    assert "UID:study-block-42" in ics_content
    assert "SUMMARY:Systems Study Session" in ics_content
    assert "END:VCALENDAR" in ics_content


# ==============================================================================
# 2. Automatic Case-Study Generation Tests
# ==============================================================================

def test_sanitize_case_study_text_strips_sensitive_data():
    dirty_text = (
        "Server failed at 192.168.1.100 for user student@domain.edu. "
        "Bearer token sk-proj-1234567890abcdef and password=SuperSecretPass "
        "stored under /home/phroggingphrog/.hermes/credentials/token.json."
    )
    cleaned = sanitize_case_study_text(dirty_text)

    assert "student@domain.edu" not in cleaned
    assert "[REDACTED_EMAIL]" in cleaned
    assert "192.168.1.100" not in cleaned
    assert "[REDACTED_IP]" in cleaned
    assert "SuperSecretPass" not in cleaned
    assert "[REDACTED_SECRET]" in cleaned
    assert "/home/phroggingphrog/.hermes/credentials" not in cleaned
    assert "[REDACTED_PATH]" in cleaned


def test_generate_case_study_from_event():
    event = LearningEvent(
        id="inc-api-key-leak-01",
        source="hermes_runtime",
        sensitivity="operational",
        whole_system="Hermes Agent",
        subsystem="Credential Manager",
        concept="Secret Redaction",
        concept_tags=("security", "redaction", "api-keys"),
        observed_fact="User dev@secretcorp.com exposed key sk-live-999888 in prompt.",
        evidence=({"source": "sanitized_service_log", "reference": "ref-1"},),
        lesson={
            "concept": "Model output filters must scrub secrets before persistence.",
            "explanation": "Always apply regex sanitizers across pipeline boundaries.",
        },
        exercise={
            "task": "Implement redactor in Python.",
        },
        verification={
            "command": "python3 -c 'print(\"SANITIZED\")'",
        },
        verified_result="Verification succeeded with SANITIZED",
    )

    case_study = generate_case_study_from_event(event)

    assert isinstance(case_study, CaseStudyModule)
    assert case_study.whole_system == "Hermes Agent"
    assert case_study.subsystem == "Credential Manager"
    assert case_study.concept == "Secret Redaction"
    assert case_study.sanitized is True

    # Check that secrets were stripped
    assert "dev@secretcorp.com" not in case_study.observed_incident
    assert "[REDACTED_EMAIL]" in case_study.observed_incident
    assert "sk-live-999888" not in case_study.observed_incident
    assert "[REDACTED_SECRET]" in case_study.observed_incident

    # Check Markdown rendering
    md = case_study.to_markdown()
    assert "# Case Study:" in md
    assert "## 1. System Architecture Context" in md
    assert "## 2. Theoretical Background & Invariants" in md
    assert "## 3. Observed Incident / Real Failure Mode" in md
    assert "## 4. Hands-On Exercise Task" in md
    assert "## 5. Verification & Acceptance Criteria" in md
    assert "python3 -c 'print(\"SANITIZED\")'" in md


def test_generate_case_studies_from_db(populated_db: Path):
    db = connect(populated_db, write=False)
    studies = generate_case_studies(db)
    db.close()

    assert len(studies) >= 1
    s0 = studies[0]
    assert s0.event_id == "incident-cron-001"
    assert s0.whole_system == "Hermes"
    assert s0.subsystem == "Scheduler"
    assert s0.concept == "Cron Idempotency"
    assert "fcnt" in s0.verification_command


# ==============================================================================
# 3. Spaced Repetition & Active-Recall Quizzes Tests
# ==============================================================================

def test_quiz_generation_and_grading(populated_db: Path):
    db = connect(populated_db, write=True)

    questions = generate_quiz_questions(db, limit=5, include_error_remediation=True)
    assert len(questions) >= 1

    # At least one should be from the error ledger hotspot
    err_q = next((q for q in questions if q.question_type == "diagnostic_remediation"), None)
    assert err_q is not None
    assert "logic" in err_q.concept

    # Test grading
    # Correct answer matching keyword
    score, is_correct, feedback = grade_quiz_answer(
        err_q, "Must verify nullability semantics in SQL standard to avoid unique constraint issues"
    )
    assert score > 0.5
    assert is_correct is True

    # Empty answer
    score_empty, is_correct_empty, _ = grade_quiz_answer(err_q, "")
    assert score_empty == 0.0
    assert is_correct_empty is False

    # Test submit_quiz_answer updating FSRS
    attempt_res = submit_quiz_answer(
        db,
        err_q,
        "Verify nullability semantics in SQL standard",
    )
    assert attempt_res.is_correct is True
    assert attempt_res.rating in {"GOOD", "EASY"}

    db.close()


def test_quiz_session_batch_runner(populated_db: Path):
    db = connect(populated_db, write=True)

    questions = generate_quiz_questions(db, limit=2)
    assert len(questions) > 0

    answers = {
        questions[0].id: questions[0].expected_answer,
    }

    summary = run_quiz_session(db, questions, answers)
    assert summary.total_questions == len(questions)
    assert summary.correct_count >= 1
    assert summary.average_score > 0.0
    assert len(summary.results) == len(questions)

    db.close()

def test_quiz_question_public_vs_private_serialization(populated_db: Path):
    db = connect(populated_db, write=False)
    questions = generate_quiz_questions(db, limit=2)
    db.close()
    assert len(questions) > 0
    q = questions[0]

    # Public serialization must NEVER expose rubric or expected answer
    pub_dict = q.to_public_dict()
    assert "expected_answer" not in pub_dict
    assert "rubric_keywords" not in pub_dict
    assert "id" in pub_dict
    assert "prompt" in pub_dict
    assert "concept" in pub_dict

    default_dict = q.to_dict()
    assert "expected_answer" not in default_dict
    assert "rubric_keywords" not in default_dict

    # Private / full serialization exposes rubric
    full_dict = q.to_full_dict()
    assert "expected_answer" in full_dict
    assert "rubric_keywords" in full_dict
    assert full_dict["expected_answer"] == q.expected_answer

    explicit_full = q.to_dict(include_rubric=True)
    assert explicit_full["expected_answer"] == q.expected_answer
    assert "rubric_keywords" in explicit_full



# ==============================================================================
# 4. Learning Dashboard & Progress Analytics Tests
# ==============================================================================

def test_generate_dashboard_report(populated_db: Path):
    db = connect(populated_db, write=False)

    report = generate_dashboard_report(db)
    db.close()

    assert isinstance(report, DashboardReport)
    d = report.to_dict()

    # Verify BKT Mastery section with actual seeded mastery values
    assert "mastery" in d
    assert d["mastery"]["total_objectives"] >= 1
    assert d["mastery"]["mean_p_mastery"] > 0.0
    rel_obj = next(o for o in d["mastery"]["objectives"] if o["objective_id"] == "db:relational-basics:v1")
    assert rel_obj["p_mastery"] > 0.5
    assert rel_obj["gate_passed"] is True
    assert rel_obj["attempts"] >= 5
    assert "gate PASSED" in rel_obj["reason"]
    assert d["mastery"]["solid_count"] >= 1
    # Verify Spaced Retrieval section
    assert "spaced_retrieval" in d
    assert "total_reviews" in d["spaced_retrieval"]

    # Verify Error Taxonomy section
    assert "error_taxonomy" in d
    assert d["error_taxonomy"]["total_errors"] >= 1
    assert "logic" in d["error_taxonomy"]["category_counts"]

    # Verify Systems Engineering Learning Events section
    assert "learning_events" in d
    assert d["learning_events"]["total_events"] >= 1
    assert "Hermes" in d["learning_events"]["by_system"]

    # Verify ASCII text formatting
    text = report.to_text()
    assert "HERMES LEARNING DASHBOARD" in text
    assert "[1] BKT MASTERY SUMMARY" in text
    assert "[2] FSRS SPACED RETRIEVAL QUEUES" in text
    assert "[3] ERROR TAXONOMY HOTSPOTS" in text
    assert "[4] SYSTEMS ENGINEERING LEARNING EVENTS" in text


# ==============================================================================
# 5. Public API and CLI Entrypoint Tests
# ==============================================================================

def test_api_public_functions(populated_db: Path):
    # Case study API
    case_studies = api.generate_case_study(db_path=populated_db)
    assert len(case_studies) >= 1
    assert case_studies[0]["whole_system"] == "Hermes"

    # Quiz API
    quiz_items = api.generate_quiz(db_path=populated_db, limit=3)
    assert len(quiz_items) >= 1
    assert "expected_answer" not in quiz_items[0]
    assert "rubric_keywords" not in quiz_items[0]

    # Submit quiz
    sub_res = api.submit_quiz(
        question_dict=quiz_items[0],
        user_answer="partial guess",
        db_path=populated_db,
    )
    assert "expected_answer" in sub_res
    assert "rubric_keywords" in sub_res
    revealed_exp = sub_res["expected_answer"]

    sub_res_correct = api.submit_quiz(
        question_dict=quiz_items[0],
        user_answer=revealed_exp,
        db_path=populated_db,
    )
    assert sub_res_correct["is_correct"] is True

    # Dashboard API
    dash_dict = api.progress_dashboard(db_path=populated_db)
    assert "mastery" in dash_dict
    assert "learning_events" in dash_dict

    dash_text = api.render_dashboard_text(db_path=populated_db)
    assert "HERMES LEARNING DASHBOARD" in dash_text


def test_cli_p2_commands(populated_db: Path, capsys: pytest.CaptureFixture):
    from learning_tracking.academia_learning.__main__ import main as academia_main

    # Test case-study CLI
    ret = academia_main(["--db-path", str(populated_db), "case-study", "--format", "json"])
    assert ret == 0
    out = capsys.readouterr().out
    assert "case_studies" in out

    # Test quiz CLI
    ret = academia_main(["--db-path", str(populated_db), "quiz", "--limit", "2"])
    assert ret == 0
    out = capsys.readouterr().out
    assert "questions" in out

    # Test dashboard CLI
    ret = academia_main(["--db-path", str(populated_db), "dashboard", "--format", "json"])
    assert ret == 0
    out = capsys.readouterr().out
    assert "mastery" in out

    # Test top-level dispatcher in learning_tracking.__main__
    from learning_tracking.__main__ import main as root_main
    ret_root = root_main(["--db-path", str(populated_db), "dashboard", "--format", "text"])
    assert ret_root == 0
    out_root = capsys.readouterr().out
    assert "HERMES LEARNING DASHBOARD" in out_root


def test_calendar_status_detects_configured_sources_safely(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    # 1. Credential file on disk is ignored without env var or injected config
    fake_token_file = tmp_path / "google_token.json"
    fake_token_file.write_text(json.dumps({"access_token": "secret_token_on_disk"}))
    status_unconfigured = get_calendar_provider_status(hermes_dir=tmp_path)
    assert status_unconfigured.google_available is False
    assert status_unconfigured.caldav_available is False

    # 2. GOOGLE_CALENDAR_ACCESS_TOKEN env must be detected as available
    monkeypatch.setenv("GOOGLE_CALENDAR_ACCESS_TOKEN", "dummy_secret_not_revealed")
    status = get_calendar_provider_status(hermes_dir=tmp_path)
    assert status.google_available is True
    assert status.google_source == "env:GOOGLE_CALENDAR_ACCESS_TOKEN"
    # Ensure dummy_secret_not_revealed is NEVER in status dict
    status_dump = json.dumps(status.to_dict())
    assert "dummy_secret_not_revealed" not in status_dump

    # 3. GOOGLE_CALENDAR_CLIENT_CONFIG env detection
    monkeypatch.delenv("GOOGLE_CALENDAR_ACCESS_TOKEN", raising=False)
    monkeypatch.setenv("GOOGLE_CALENDAR_CLIENT_CONFIG", '{"client_id": "test-client-id"}')
    status_cfg = get_calendar_provider_status(hermes_dir=tmp_path)
    assert status_cfg.google_available is True
    assert status_cfg.google_source == "env:GOOGLE_CALENDAR_CLIENT_CONFIG"
    assert "test-client-id" not in json.dumps(status_cfg.to_dict())

    # 4. Test CalDAV env detection
    monkeypatch.delenv("GOOGLE_CALENDAR_CLIENT_CONFIG", raising=False)
    monkeypatch.setenv("CALDAV_URL", "https://caldav.example.com/dav")
    monkeypatch.setenv("CALDAV_USERNAME", "study_user")
    monkeypatch.setenv("CALDAV_PASSWORD", "caldav_secret_pass")
    status2 = get_calendar_provider_status(hermes_dir=tmp_path)
    assert status2.caldav_available is True
    assert status2.caldav_source == "env:CALDAV_URL"
    assert "caldav_secret_pass" not in json.dumps(status2.to_dict())

    # 5. Test explicit injected config
    status_injected = get_calendar_provider_status(config={"GOOGLE_CALENDAR_ACCESS_TOKEN": "injected_token"})
    assert status_injected.google_available is True
    assert "injected_token" not in json.dumps(status_injected.to_dict())

def test_calendar_unconfigured_failure_and_base32hex_id(tmp_path: Path):
    # Test unconfigured raises CalendarProviderUnconfiguredError
    status = get_calendar_provider_status(hermes_dir=tmp_path)
    assert status.google_available is False
    assert status.caldav_available is False

    g_prov = GoogleCalendarProvider(status=status)
    with pytest.raises(CalendarProviderUnconfiguredError):
        g_prov.read_events()

    c_prov = CalDAVCalendarProvider(status=status)
    with pytest.raises(CalendarProviderUnconfiguredError):
        c_prov.read_events()

    # Test base32hex ID formatting & determinism
    ev = CalendarEvent(
        uid="review-cron-idempotency-123@hermes.learning",
        summary="Review Cron Idempotency",
        description="Advisory lock testing",
        start_time=datetime.now(timezone.utc),
        end_time=datetime.now(timezone.utc),
    )
    payload1 = ev.to_google_payload()
    payload2 = ev.to_google_payload()
    assert payload1["id"] == payload2["id"]
    # Must be valid base32hex: only lowercase a-v and 0-9, length 5-1024
    assert re.match(r"^[a-v0-9]{5,1024}$", payload1["id"])

def test_calendar_provider_error_sanitization(monkeypatch: pytest.MonkeyPatch):
    import urllib.request
    from unittest.mock import MagicMock

    def mock_urlopen_google(req, **kwargs):
        raise RuntimeError("HTTP 401 Unauthorized: Bearer super_secret_access_token_12345 invalid from /home/phroggingphrog/.hermes/google_token.json")

    monkeypatch.setattr(urllib.request, "urlopen", mock_urlopen_google)
    g_prov = GoogleCalendarProvider(config={"GOOGLE_CALENDAR_ACCESS_TOKEN": "super_secret_access_token_12345"})
    ev = CalendarEvent(
        uid="ev-123",
        summary="Review Task",
        description="Review description",
        start_time=datetime.now(timezone.utc),
        end_time=datetime.now(timezone.utc) + timedelta(minutes=30),
    )
    res_g = g_prov.sync_events([ev])
    assert res_g["status"] == "error"
    assert "super_secret_access_token_12345" not in res_g["error"]
    assert "/home/phroggingphrog/.hermes" not in res_g["error"]
    assert "[REDACTED_SECRET]" in res_g["error"]
    assert "[REDACTED_PATH]" in res_g["error"]

    def mock_urlopen_caldav(req, **kwargs):
        raise RuntimeError("HTTP 403 Forbidden for user study_user with Basic c3R1ZHlfdXNlcjpzdXBlcl9zZWNyZXRfcGFzc3dvcmQ= on ~/.hermes/credentials")

    monkeypatch.setattr(urllib.request, "urlopen", mock_urlopen_caldav)
    c_prov = CalDAVCalendarProvider(config={
        "CALDAV_URL": "https://caldav.example.com/dav",
        "CALDAV_USERNAME": "study_user",
        "CALDAV_PASSWORD": "super_secret_password",
    })
    res_c = c_prov.sync_events([ev])
    assert res_c["status"] == "error"
    assert "super_secret_password" not in res_c["error"]
    assert "~/.hermes" not in res_c["error"]
    assert "[REDACTED_SECRET]" in res_c["error"]
    assert "[REDACTED_PATH]" in res_c["error"]


def test_calendar_read_events_sanitized_error_propagation_and_404(monkeypatch: pytest.MonkeyPatch):
    import urllib.request
    import urllib.error

    # Google 401 error with leaked secret token -> raises CalendarProviderError, redacted
    def mock_urlopen_google_fail(req, **kwargs):
        raise urllib.error.HTTPError(
            req.full_url,
            401,
            "HTTP 401 Unauthorized: token=super_secret_access_token_12345 in /home/phroggingphrog/.hermes/creds.json",
            {},
            None,
        )

    monkeypatch.setattr(urllib.request, "urlopen", mock_urlopen_google_fail)
    g_prov = GoogleCalendarProvider(config={"GOOGLE_CALENDAR_ACCESS_TOKEN": "super_secret_access_token_12345"})
    with pytest.raises(CalendarProviderError) as exc_info:
        g_prov.read_events()
    err_msg = str(exc_info.value)
    assert "super_secret_access_token_12345" not in err_msg
    assert "/home/phroggingphrog/.hermes" not in err_msg
    assert "[REDACTED_SECRET]" in err_msg
    assert "[REDACTED_PATH]" in err_msg

    # Google 404 (calendar empty/not found) -> returns []
    def mock_urlopen_google_404(req, **kwargs):
        raise urllib.error.HTTPError(req.full_url, 404, "Not Found", {}, None)

    monkeypatch.setattr(urllib.request, "urlopen", mock_urlopen_google_404)
    items = g_prov.read_events()
    assert items == []

    # CalDAV 500 error with leaked password -> raises CalendarProviderError, redacted
    def mock_urlopen_caldav_fail(req, **kwargs):
        raise urllib.error.HTTPError(
            req.full_url,
            500,
            "Internal Error: password=super_secret_password failed",
            {},
            None,
        )

    monkeypatch.setattr(urllib.request, "urlopen", mock_urlopen_caldav_fail)
    c_prov = CalDAVCalendarProvider(config={
        "CALDAV_URL": "https://caldav.example.com/dav",
        "CALDAV_USERNAME": "cal_user",
        "CALDAV_PASSWORD": "super_secret_password",
    })
    with pytest.raises(CalendarProviderError) as exc_info:
        c_prov.read_events()
    err_msg = str(exc_info.value)
    assert "super_secret_password" not in err_msg
    assert "[REDACTED_SECRET]" in err_msg

    # CalDAV 404 -> returns []
    def mock_urlopen_caldav_404(req, **kwargs):
        raise urllib.error.HTTPError(req.full_url, 404, "Not Found", {}, None)

    monkeypatch.setattr(urllib.request, "urlopen", mock_urlopen_caldav_404)
    items_c = c_prov.read_events()
    assert items_c == []


def test_calendar_sync_409_idempotent_reconciliation_via_get(monkeypatch: pytest.MonkeyPatch):
    import urllib.request
    import urllib.error
    from unittest.mock import MagicMock

    ev = CalendarEvent(
        uid="idempotent-study-item-409@hermes.learning",
        summary="Idempotent Review Block",
        description="Testing 409 conflict handling",
        start_time=datetime.now(timezone.utc),
        end_time=datetime.now(timezone.utc) + timedelta(minutes=30),
    )
    payload = ev.to_google_payload()
    expected_google_id = payload["id"]

    # Google Calendar 409 conflict: POST raises 409, then GET verifies existing event
    calls = []

    def mock_urlopen_google_409(req, **kwargs):
        calls.append((req.get_method(), req.full_url))
        if req.get_method() == "POST":
            raise urllib.error.HTTPError(req.full_url, 409, "Conflict: event already exists", {}, None)
        elif req.get_method() == "GET":
            resp = MagicMock()
            resp.read.return_value = json.dumps({
                "id": expected_google_id,
                "summary": "Idempotent Review Block",
                "status": "confirmed",
            }).encode("utf-8")
            resp.__enter__.return_value = resp
            return resp
        raise NotImplementedError

    monkeypatch.setattr(urllib.request, "urlopen", mock_urlopen_google_409)
    g_prov = GoogleCalendarProvider(config={"GOOGLE_CALENDAR_ACCESS_TOKEN": "valid_token_abc"})
    sync_res = g_prov.sync_events([ev])

    assert sync_res["status"] == "success"
    assert sync_res["synced_count"] == 1
    assert sync_res["events"][0]["google_id"] == expected_google_id
    assert sync_res["events"][0]["status"] == "confirmed"
    assert len(calls) == 2
    assert calls[0][0] == "POST"
    assert calls[1][0] == "GET"

    # CalDAV 409/412 conflict: PUT raises 409, then GET verifies existing VEVENT UID
    caldav_calls = []

    def mock_urlopen_caldav_409(req, **kwargs):
        caldav_calls.append((req.get_method(), req.full_url))
        if req.get_method() == "PUT":
            raise urllib.error.HTTPError(req.full_url, 409, "Conflict", {}, None)
        elif req.get_method() == "GET":
            resp = MagicMock()
            resp.status = 200
            resp.read.return_value = f"BEGIN:VCALENDAR\r\nBEGIN:VEVENT\r\nUID:{ev.uid}\r\nEND:VEVENT\r\nEND:VCALENDAR".encode("utf-8")
            resp.__enter__.return_value = resp
            return resp
        raise NotImplementedError

    monkeypatch.setattr(urllib.request, "urlopen", mock_urlopen_caldav_409)
    c_prov = CalDAVCalendarProvider(config={
        "CALDAV_URL": "https://caldav.example.com/dav",
        "CALDAV_USERNAME": "cal_user",
        "CALDAV_PASSWORD": "password123",
    })
    sync_res_c = c_prov.sync_events([ev])
    assert sync_res_c["status"] == "success"
    assert sync_res_c["synced_count"] == 1
    assert sync_res_c["events"][0]["uid"] == ev.uid
    assert sync_res_c["events"][0]["status"] == "confirmed"
    assert len(caldav_calls) == 2
    assert caldav_calls[0][0] == "PUT"
    assert caldav_calls[1][0] == "GET"


def test_calendar_sync_remote_event_id_verification(monkeypatch: pytest.MonkeyPatch):
    import urllib.request
    from unittest.mock import MagicMock

    ev = CalendarEvent(
        uid="verify-id-event-1@hermes.learning",
        summary="Remote ID Verification",
        description="Testing verification of event ID",
        start_time=datetime.now(timezone.utc),
        end_time=datetime.now(timezone.utc) + timedelta(minutes=30),
    )
    payload = ev.to_google_payload()
    expected_id = payload["id"]

    # 1. Verification via confirmation response (POST returns matching id)
    def mock_urlopen_google_confirm(req, **kwargs):
        resp = MagicMock()
        resp.read.return_value = json.dumps({"id": expected_id, "status": "confirmed"}).encode("utf-8")
        resp.__enter__.return_value = resp
        return resp

    monkeypatch.setattr(urllib.request, "urlopen", mock_urlopen_google_confirm)
    g_prov = GoogleCalendarProvider(config={"GOOGLE_CALENDAR_ACCESS_TOKEN": "token_xyz"})
    res = g_prov.sync_events([ev])
    assert res["status"] == "success"
    assert res["events"][0]["google_id"] == expected_id

    # 2. Verification via GET when confirmation response is missing id
    get_called = []

    def mock_urlopen_google_get_verify(req, **kwargs):
        resp = MagicMock()
        if req.get_method() == "POST":
            # Response without id
            resp.read.return_value = json.dumps({"status": "confirmed"}).encode("utf-8")
        elif req.get_method() == "GET":
            get_called.append(req.full_url)
            resp.read.return_value = json.dumps({"id": expected_id, "status": "confirmed"}).encode("utf-8")
        resp.__enter__.return_value = resp
        return resp

    monkeypatch.setattr(urllib.request, "urlopen", mock_urlopen_google_get_verify)
    res = g_prov.sync_events([ev])
    assert res["status"] == "success"
    assert res["events"][0]["google_id"] == expected_id
    assert len(get_called) == 1

    # 3. Verification failure when ID mismatches and GET verification fails
    def mock_urlopen_google_mismatch(req, **kwargs):
        resp = MagicMock()
        if req.get_method() == "POST":
            resp.read.return_value = json.dumps({"id": "completely_wrong_id", "status": "confirmed"}).encode("utf-8")
        resp.__enter__.return_value = resp
        return resp

    monkeypatch.setattr(urllib.request, "urlopen", mock_urlopen_google_mismatch)
    res = g_prov.sync_events([ev])
    assert res["status"] == "error"
    assert "verification failed" in res["error"].lower()


def test_quiz_server_side_rubric_resolution_prevents_spoofing(populated_db: Path):
    # 1. Generate real quiz
    quiz_items = api.generate_quiz(db_path=populated_db, limit=3)
    assert len(quiz_items) >= 1
    real_item = quiz_items[0]

    # 2. Attacker modifies question_dict with fake expected_answer and rubric_keywords
    spoofed_item = dict(real_item)
    spoofed_item["expected_answer"] = "attacker chosen free pass answer"
    spoofed_item["rubric_keywords"] = ["attacker", "chosen", "free", "pass"]

    # Submitting attacker answer against spoofed question_dict must NOT pass
    sub_res = api.submit_quiz(
        question_dict=spoofed_item,
        user_answer="attacker chosen free pass answer",
        db_path=populated_db,
    )
    assert sub_res["is_correct"] is False
    assert sub_res["score"] < 0.5

    # 3. Forged non-existent question must fail resolution
    forged_item = {
        "id": "q-err-nonexistent999",
        "expected_answer": "free pass",
        "rubric_keywords": ["free", "pass"],
    }
    with pytest.raises(ValueError, match="cannot be resolved server-side"):
        api.submit_quiz(
            question_dict=forged_item,
            user_answer="free pass",
            db_path=populated_db,
        )


def test_case_study_strict_verified_only_generation(populated_db: Path):
    unverified_event = LearningEvent(
        id="incident-unverified-dummy",
        source="hermes_runtime",
        sensitivity="operational",
        whole_system="Hermes",
        subsystem="Sandbox",
        concept="Process Isolation",
        concept_tags=("isolation", "security"),
        observed_fact="Process escaped sandbox environment.",
        evidence=({"source": "service_log", "reference": "ref-sandbox-err"},),
        lesson={"concept": "Namespace isolation", "explanation": "Must configure cgroups."},
        exercise={"task": "Apply cgroups isolation"},
        verification={"command": "check_cgroups.sh"},
        verified_result=None,
    )

    # 1. Direct call on unverified event must raise UnverifiedEventError
    with pytest.raises(UnverifiedEventError):
        generate_case_study_from_event(unverified_event)

    db = connect(populated_db, write=True)
    insert_learning_event(db, unverified_event)
    db.commit()

    # 2. Targeted generation on unverified event ID must raise UnverifiedEventError
    with pytest.raises(UnverifiedEventError):
        generate_case_studies(db, event_id="incident-unverified-dummy")

    # 3. Batch generation skips unverified events
    studies = generate_case_studies(db)
    assert all(s.event_id != "incident-unverified-dummy" for s in studies)
    assert all(s.expected_result != "Exit code 0 and successful verification output" for s in studies)
    db.close()

def test_cli_quiz_answers_json(populated_db: Path, capsys: pytest.CaptureFixture, tmp_path: Path):
    from learning_tracking.academia_learning.__main__ import main as academia_main

    # 1. Fetch questions
    ret = academia_main(["--db-path", str(populated_db), "quiz", "--limit", "1"])
    assert ret == 0
    out = capsys.readouterr().out
    data = json.loads(out)
    q = data["questions"][0]
    q_id = q["id"]
    assert "expected_answer" not in q
    assert "rubric_keywords" not in q

    db = connect(populated_db, write=False)
    try:
        resolved = resolve_quiz_rubric(db, q)
        exp = resolved.expected_answer
    finally:
        db.close()
    # 2. Write answers json
    answers_file = tmp_path / "answers.json"
    answers_file.write_text(json.dumps({q_id: exp}), encoding="utf-8")

    # 3. Submit via CLI
    ret_ans = academia_main(["--db-path", str(populated_db), "quiz", "--answers-json", str(answers_file)])
    assert ret_ans == 0
    out_ans = capsys.readouterr().out
    res_data = json.loads(out_ans)
    assert res_data["status"] == "OK"
    assert res_data["session"]["correct_count"] == 1
    assert "expected_answer" in res_data["session"]["results"][0]


def test_cli_schedule_calendar_sync(populated_db: Path, capsys: pytest.CaptureFixture, tmp_path: Path):
    from learning_tracking.academia_learning.__main__ import main as academia_main
    ics_out = tmp_path / "out.ics"

    ret = academia_main([
        "--db-path", str(populated_db),
        "schedule-calendar",
        "--course-id", "course:relational-modeling:v1",
        "--ics-output", str(ics_out),
        "--sync",
    ])
    assert ret == 0
    out = capsys.readouterr().out
    data = json.loads(out)
    assert data["status"] == "OK"
    assert "sync_result" in data
    assert data["sync_result"]["status"] == "FALLBACK_ICS"
    assert ics_out.exists()
