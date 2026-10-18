"""Academia's explicit learning entry point.

    python -m learning_tracking.academia_learning start-learning
    python -m learning_tracking.academia_learning record-attempt --objective ... --confidence-after 2 ...
    python -m learning_tracking.academia_learning resume --objective ...

Every invocation is explicit -- there is no global, every-turn hook here.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import api

def _cmd_record_incident(args: argparse.Namespace) -> int:
    """Generate, validate, and explicitly commit one incident learning event."""
    from .curriculum import generate_learning_event_from_incident

    event = generate_learning_event_from_incident(
        event_id=args.event_id,
        system=args.system,
        subsystem=args.subsystem,
        concept=args.concept,
        concept_tags=args.concept_tag or [],
        observed_fact=args.observed_fact,
        evidence_source=args.evidence_source,
        evidence_reference=args.evidence_reference,
        explanation=args.explanation,
        exercise_task=args.exercise_task,
        verification_command=args.verification_command,
        evidence_file_path=args.evidence_file,
        sensitivity=args.sensitivity,
        safe_roots=args.safe_root,
        incident_or_change_ref=args.incident_or_change_ref,
        hypothesis=args.hypothesis,
        model_explanation=args.model_explanation,
        verified_result=args.verified_result,
        sanitized_case_summary=args.sanitized_case_summary,
    )
    receipt = api.record_learning_event(
        event,
        dry_run=not args.commit,
        db_path=args.db_path,
        policy_path=args.policy_path,
        safe_roots=args.safe_root,
    )
    print(json.dumps({"status": "OK", "receipt": receipt}, indent=2))
    return 0


def _cmd_start_learning(args: argparse.Namespace) -> int:
    try:
        block = api.start_block(
            db_path=args.db_path,
            canvas_summary_path=args.canvas_summary,
            require_citations=args.require_citations,
        )
    except api.InsufficientEvidenceError as exc:
        print(json.dumps({"status": "INSUFFICIENT_EVIDENCE", "detail": str(exc)}, indent=2))
        return 1
    print(json.dumps({"status": "OK", "block": block}, indent=2))
    return 0


def _cmd_record_attempt(args: argparse.Namespace) -> int:
    receipt = api.append_attempt(
        objective_version_id=args.objective,
        response_ref=args.response_ref,
        assistance_level=args.assistance_level,
        confidence_before=args.confidence_before,
        confidence_after=args.confidence_after,
        errors=args.errors or [],
        corrections=args.corrections or [],
        source_refs=args.source_ref or [],
        next_action=args.next_action,
        exercise_version_id=args.exercise_version,
        db_path=args.db_path,
    )
    print(json.dumps({"status": "OK", "receipt": receipt}, indent=2))
    return 0


def _cmd_resume(args: argparse.Namespace) -> int:
    progress = api.retrieve_evidence(args.objective, db_path=args.db_path)
    print(json.dumps({"status": "OK", "progress": progress}, indent=2))
    return 0


def _cmd_mastery_status(args: argparse.Namespace) -> int:
    status = api.mastery_status(args.objective, db_path=args.db_path)
    print(json.dumps({"status": "OK", "mastery": status}, indent=2))
    return 0


def _cmd_ingest_source(args: argparse.Namespace) -> int:
    from . import ingestion
    from .db import connect

    chunk_specs = [
        ingestion.ChunkSpec(start_line=s, end_line=e) for s, e in args.chunk
    ] if args.chunk else []
    if not chunk_specs:
        print(json.dumps({"status": "ERROR", "detail": "at least one --chunk START END is required"}, indent=2))
        return 1

    db_path = args.db_path or api.DEFAULT_DB_PATH
    if not args.commit:
        print(json.dumps({
            "status": "DRY_RUN",
            "would_ingest": {
                "course_id": args.course_id,
                "source_id": args.source_id,
                "file": str(args.file),
                "chunk_count": len(chunk_specs),
            },
            "note": "pass --commit to actually write; nothing was written",
        }, indent=2))
        return 0

    db = connect(db_path, write=True)
    try:
        db.execute("BEGIN IMMEDIATE")
        source_version_id = ingestion.add_source_version(
            db,
            course_id=args.course_id,
            source_id=args.source_id,
            file_path=args.file,
            chunks=chunk_specs,
        )
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
    print(json.dumps({"status": "OK", "source_version_id": source_version_id}, indent=2))
    return 0


def _cmd_ingest_objective(args: argparse.Namespace) -> int:
    from . import ingestion
    from .db import connect

    db_path = args.db_path or api.DEFAULT_DB_PATH
    if not args.commit:
        print(json.dumps({
            "status": "DRY_RUN",
            "would_ingest": {
                "course_id": args.course_id,
                "objective_id": args.objective_id,
                "definition": args.definition,
                "prerequisite_ids": args.prerequisite or [],
            },
            "note": "pass --commit to actually write; nothing was written",
        }, indent=2))
        return 0

    db = connect(db_path, write=True)
    try:
        db.execute("BEGIN IMMEDIATE")
        objective_version_id = ingestion.add_objective(
            db,
            course_id=args.course_id,
            objective_id=args.objective_id,
            definition=args.definition,
            prerequisite_ids=args.prerequisite or [],
        )
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
    print(json.dumps({"status": "OK", "objective_version_id": objective_version_id}, indent=2))
    return 0


def _cmd_learner_profile(args: argparse.Namespace) -> int:
    profile = api.learner_profile(db_path=args.db_path)
    if profile is None:
        print(json.dumps({"status": "NOT_SEEDED", "detail": "no learner profile has been seeded"}, indent=2))
        return 1
    print(json.dumps({"status": "OK", "profile": profile}, indent=2))
    return 0


def _cmd_plan_canvas(args: argparse.Namespace) -> int:
    from ..canvas_pipeline import plan_canvas
    res = plan_canvas(
        args.canvas_summary,
        fetch_live=getattr(args, "api", False) or args.canvas_summary is None,
        mode=args.mode,
        example_roots=args.example_root,
        db_path=args.db_path,
        policy_path=getattr(args, "policy_path", None),
        safe_roots=getattr(args, "safe_root", None),
        commit_events=args.commit,
        max_assignments=args.max_assignments,
    )
    if args.ics_output and res.get("calendar_events"):
        from ..calendar_schedule import write_icalendar, CalendarEvent
        from datetime import datetime
        events = [
            CalendarEvent(
                uid=ev["uid"],
                summary=ev["summary"],
                description=ev["description"],
                start_time=datetime.fromisoformat(ev["start_time"]),
                end_time=datetime.fromisoformat(ev["end_time"]),
            )
            for ev in res["calendar_events"]
        ]
        write_icalendar(events, args.ics_output)
    print(json.dumps(res, indent=2))
    return 0


def _cmd_record_error(args: argparse.Namespace) -> int:
    res = api.record_error(
        objective_version_id=args.objective,
        error_category=args.category,
        misconception=args.misconception,
        failed_step=args.failed_step,
        verification_command=args.verification_command,
        observed_output=args.observed_output,
        expected_output=args.expected_output,
        correction=args.correction,
        attempt_id=args.attempt_id,
        actor=args.actor,
        db_path=args.db_path,
    )
    print(json.dumps({"status": "OK", "error": res}, indent=2))
    return 0


def _cmd_list_errors(args: argparse.Namespace) -> int:
    errors = api.list_errors(
        objective_version_id=args.objective,
        error_category=args.category,
        limit=args.limit,
        db_path=args.db_path,
    )
    print(json.dumps({"status": "OK", "count": len(errors), "errors": errors}, indent=2))
    return 0


def _cmd_error_summary(args: argparse.Namespace) -> int:
    summary = api.error_summary(
        objective_version_id=args.objective,
        db_path=args.db_path,
    )
    print(json.dumps({"status": "OK", "summary": summary}, indent=2))
    return 0


def _cmd_schedule_calendar(args: argparse.Namespace) -> int:
    from ..calendar_schedule import (
        CalendarSyncManager,
        StudyWindow,
        export_icalendar,
        schedule_study_sessions,
        write_icalendar,
    )
    window = StudyWindow(
        start_hour=args.window_start_hour,
        end_hour=args.window_end_hour,
        session_duration_minutes=args.duration_minutes,
    )
    canvas_summary = args.canvas_summary
    if not canvas_summary and not args.course_id:
        from ..__main__ import DEFAULT_CANVAS
        if DEFAULT_CANVAS.exists():
            canvas_summary = DEFAULT_CANVAS

    if canvas_summary:
        from ..canvas_pipeline import plan_canvas
        res = plan_canvas(canvas_summary, db_path=args.db_path)
        items = [
            {
                "title": t["title"],
                "description": f"Concept: {t['concept']}\nAction: {t['action']}",
                "personal_final": t["scheduled_for"],
            }
            for t in res.get("review_tasks", [])
        ]
        events = schedule_study_sessions(items, study_window=window)
    elif args.course_id:
        from .spaced_retrieval import get_due_reviews
        db = api.connect(args.db_path or api.DEFAULT_DB_PATH, write=False)
        try:
            due_reviews = get_due_reviews(db, args.course_id)
            events = schedule_study_sessions(due_reviews, study_window=window)
        finally:
            db.close()
    else:
        print(json.dumps({"status": "ERROR", "detail": "Either --canvas-summary or --course-id required"}, indent=2))
        return 1

    sync_result = None
    if getattr(args, "sync", False):
        mgr = CalendarSyncManager()
        sync_result = mgr.sync(events, preferred_provider=args.provider, ics_output_path=args.ics_output)
    elif args.ics_output:
        write_icalendar(events, args.ics_output)

    if getattr(args, "format", "json") == "ics":
        print(export_icalendar(events))
        return 0
    resp: dict[str, Any] = {
        "status": "OK",
        "event_count": len(events),
        "events": [e.to_dict() for e in events],
        "ics_exported": str(args.ics_output) if args.ics_output else None,
    }
    if sync_result:
        resp["sync_result"] = sync_result

    print(json.dumps(resp, indent=2))
    return 0


def _cmd_generate_case_study(args: argparse.Namespace) -> int:
    from .case_study import UnverifiedEventError, generate_case_studies
    db = api.connect(args.db_path or api.DEFAULT_DB_PATH, write=False)
    try:
        req_verified = not getattr(args, "allow_unverified", False)
        studies = generate_case_studies(
            db,
            event_id=args.event_id,
            source=args.source,
            require_verified=req_verified,
        )
    except UnverifiedEventError as exc:
        print(json.dumps({"status": "ERROR", "error": str(exc)}, indent=2))
        return 1
    finally:
        db.close()
    if not studies:
        out = {"status": "EMPTY", "message": "No matching learning events found"}
        print(json.dumps(out, indent=2))
        return 0

    if args.format in ("markdown", "text"):
        md_text = "\n---\n\n".join(s.to_markdown() for s in studies)
        if args.output:
            Path(args.output).write_text(md_text, encoding="utf-8")
        print(md_text)
    elif args.format == "summary":
        summary = [
            {"id": s.id, "title": s.title, "system": s.whole_system, "concept": s.concept}
            for s in studies
        ]
        res = {"status": "OK", "count": len(studies), "case_studies": summary}
        if args.output:
            Path(args.output).write_text(json.dumps(res, indent=2), encoding="utf-8")
        print(json.dumps(res, indent=2))
    else:  # json
        res = {"status": "OK", "count": len(studies), "case_studies": [s.to_dict() for s in studies]}
        if args.output:
            Path(args.output).write_text(json.dumps(res, indent=2), encoding="utf-8")
        print(json.dumps(res, indent=2))
    return 0


def _cmd_review_quiz(args: argparse.Namespace) -> int:
    from .quiz import generate_quiz_questions, run_quiz_session
    db = api.connect(args.db_path or api.DEFAULT_DB_PATH, write=True)
    try:
        questions = generate_quiz_questions(
            db,
            course_id=args.course_id,
            limit=args.limit,
            include_error_remediation=not args.no_remediation,
        )
        if not questions:
            print(json.dumps({"status": "EMPTY", "message": "No review questions generated."}, indent=2))
            return 0

        if args.answers_json:
            path = Path(args.answers_json)
            answers_data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else json.loads(args.answers_json)
            db.execute("BEGIN IMMEDIATE")
            summary = run_quiz_session(db, questions, answers_data)
            db.commit()
            print(json.dumps({"status": "OK", "session": summary.to_dict()}, indent=2))
            return 0

        if args.interactive:
            print(f"\n=== Active-Recall Quiz ({len(questions)} questions) ===")
            answers: dict[str, str] = {}
            for i, q in enumerate(questions, 1):
                print(f"\n[{i}/{len(questions)}] Concept: {q.concept}")
                print(f"{q.prompt}")
                ans = input("\nYour Answer > ").strip()
                answers[q.id] = ans
            db.execute("BEGIN IMMEDIATE")
            summary = run_quiz_session(db, questions, answers)
            db.commit()
            print(f"\n=== Quiz Complete: Score {summary.average_score:.2f} ({summary.correct_count}/{summary.total_questions} correct) ===")
            for res in summary.results:
                print(f"- Question {res.question_id}: {'PASS' if res.is_correct else 'FAIL'} (Rating: {res.rating}, Next Interval: {res.next_interval_days}d)")
            return 0

        print(json.dumps({
            "status": "OK",
            "question_count": len(questions),
            "questions": [q.to_public_dict() for q in questions],
            "note": "Pass --interactive to answer live, or --answers-json to grade answers and update FSRS.",
        }, indent=2))
        return 0
    finally:
        db.close()


def _cmd_dashboard(args: argparse.Namespace) -> int:
    from .dashboard import generate_dashboard_report
    db = api.connect(args.db_path or api.DEFAULT_DB_PATH, write=False)
    try:
        report = generate_dashboard_report(db, course_id=args.course_id)
    finally:
        db.close()

    if args.format == "json":
        output = json.dumps(report.to_dict(), indent=2)
    else:
        output = report.to_text()

    if args.output:
        Path(args.output).write_text(output, encoding="utf-8")
    print(output)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="academia-learning")
    parser.add_argument("--db-path", type=Path, default=None)
    sub = parser.add_subparsers(dest="command", required=True)

    p_start = sub.add_parser("start-learning")
    p_start.add_argument("--canvas-summary", type=Path, default=None)
    p_start.add_argument("--require-citations", action="store_true")
    p_start.set_defaults(func=_cmd_start_learning)

    p_attempt = sub.add_parser("record-attempt")
    p_attempt.add_argument("--objective", required=True)
    p_attempt.add_argument("--exercise-version", default=None)
    p_attempt.add_argument("--response-ref", required=True)
    p_attempt.add_argument("--assistance-level", default="user-provided")
    p_attempt.add_argument("--confidence-before", type=int, default=None)
    p_attempt.add_argument("--confidence-after", type=int, required=True)
    p_attempt.add_argument("--errors", action="append")
    p_attempt.add_argument("--corrections", action="append")
    p_attempt.add_argument("--source-ref", action="append")
    p_attempt.add_argument("--next-action", required=True)
    p_attempt.set_defaults(func=_cmd_record_attempt)

    p_resume = sub.add_parser("resume")
    p_resume.add_argument("--objective", required=True)
    p_resume.set_defaults(func=_cmd_resume)

    p_mastery = sub.add_parser("mastery-status")
    p_mastery.add_argument("--objective", required=True)
    p_mastery.set_defaults(func=_cmd_mastery_status)

    p_ingest_source = sub.add_parser("ingest-source")
    p_ingest_source.add_argument("--course-id", required=True)
    p_ingest_source.add_argument("--source-id", required=True)
    p_ingest_source.add_argument("--file", type=Path, required=True)
    p_ingest_source.add_argument(
        "--chunk", nargs=2, type=int, action="append", metavar=("START_LINE", "END_LINE"),
        help="1-indexed inclusive line range; repeatable for multiple chunks",
    )
    p_ingest_source.add_argument("--commit", action="store_true", help="actually write; default is dry-run")
    p_ingest_source.set_defaults(func=_cmd_ingest_source)

    p_ingest_objective = sub.add_parser("ingest-objective")
    p_ingest_objective.add_argument("--course-id", required=True)
    p_ingest_objective.add_argument("--objective-id", required=True)
    p_ingest_objective.add_argument("--definition", required=True)
    p_ingest_objective.add_argument("--prerequisite", action="append", help="existing objective_version_id; repeatable")
    p_ingest_objective.add_argument("--commit", action="store_true", help="actually write; default is dry-run")
    p_ingest_objective.set_defaults(func=_cmd_ingest_objective)

    p_profile = sub.add_parser("learner-profile")
    p_profile.set_defaults(func=_cmd_learner_profile)

    p_incident = sub.add_parser(
        "record-incident",
        help="generate and validate an incident event; pass --commit to write it",
    )
    p_incident.add_argument("--event-id", required=True)
    p_incident.add_argument("--system", required=True)
    p_incident.add_argument("--subsystem", required=True)
    p_incident.add_argument("--concept", required=True)
    p_incident.add_argument("--concept-tag", action="append", default=[])
    p_incident.add_argument("--observed-fact", required=True)
    p_incident.add_argument("--evidence-source", required=True)
    p_incident.add_argument("--evidence-reference", required=True)
    p_incident.add_argument("--explanation", required=True)
    p_incident.add_argument("--exercise-task", required=True)
    p_incident.add_argument("--verification-command", required=True)
    p_incident.add_argument("--evidence-file", type=Path, default=None)
    p_incident.add_argument("--safe-root", type=Path, action="append", default=None)
    p_incident.add_argument("--sensitivity", default="operational")
    p_incident.add_argument("--incident-or-change-ref", default=None)
    p_incident.add_argument("--hypothesis", default=None)
    p_incident.add_argument("--model-explanation", default=None)
    p_incident.add_argument("--verified-result", default=None)
    p_incident.add_argument("--sanitized-case-summary", action="store_true")
    p_incident.add_argument("--policy-path", type=Path, default=None)
    p_incident.add_argument("--commit", action="store_true", help="actually write; default is dry-run")
    p_incident.set_defaults(func=_cmd_record_incident)

    p_plan_canvas = sub.add_parser("plan-canvas", help="run Canvas to learning plan pipeline")
    p_plan_canvas.add_argument("--canvas-summary", type=Path, default=None, help="path to canvas summary JSON; if omitted or --api is set, fetches via live Canvas API")
    p_plan_canvas.add_argument("--api", action="store_true", help="fetch live assignments from Canvas REST API")
    p_plan_canvas.add_argument("--mode", default="start-learning", choices=("start-learning", "start-assignment", "start-brainstorm"))
    p_plan_canvas.add_argument("--example-root", type=Path, action="append")
    p_plan_canvas.add_argument("--commit", action="store_true", help="commit generated learning events to learning.sqlite3")
    p_plan_canvas.add_argument("--ics-output", type=Path, default=None, help="export scheduled study sessions to .ics file")
    p_plan_canvas.add_argument("--max-assignments", type=int, default=5)
    p_plan_canvas.add_argument("--policy-path", type=Path, default=None)
    p_plan_canvas.add_argument("--safe-root", type=Path, action="append", default=None)
    p_plan_canvas.set_defaults(func=_cmd_plan_canvas)

    p_rec_err = sub.add_parser("record-error", help="record an error or misconception")
    p_rec_err.add_argument("--objective", required=True)
    p_rec_err.add_argument("--category", required=True)
    p_rec_err.add_argument("--misconception", required=True)
    p_rec_err.add_argument("--failed-step", default=None)
    p_rec_err.add_argument("--verification-command", default=None)
    p_rec_err.add_argument("--observed-output", default=None)
    p_rec_err.add_argument("--expected-output", default=None)
    p_rec_err.add_argument("--correction", default=None)
    p_rec_err.add_argument("--attempt-id", default=None)
    p_rec_err.add_argument("--actor", default="user-provided")
    p_rec_err.set_defaults(func=_cmd_record_error)

    p_list_err = sub.add_parser("list-errors", help="list recorded errors")
    p_list_err.add_argument("--objective", default=None)
    p_list_err.add_argument("--category", default=None)
    p_list_err.add_argument("--limit", type=int, default=50)
    p_list_err.set_defaults(func=_cmd_list_errors)

    p_err_sum = sub.add_parser("error-summary", help="summarize recorded errors")
    p_err_sum.add_argument("--objective", default=None)
    p_err_sum.set_defaults(func=_cmd_error_summary)

    p_sched_cal = sub.add_parser("schedule-calendar", help="schedule study/review sessions into calendar")
    p_sched_cal.add_argument("--canvas-summary", type=Path, default=None)
    p_sched_cal.add_argument("--course-id", default=None)
    p_sched_cal.add_argument("--duration-minutes", type=int, default=30)
    p_sched_cal.add_argument("--window-start-hour", type=int, default=18)
    p_sched_cal.add_argument("--window-end-hour", type=int, default=21)
    p_sched_cal.add_argument("--ics-output", type=Path, default=None)
    p_sched_cal.set_defaults(func=_cmd_schedule_calendar)
    p_sched_cal.add_argument("--sync", action="store_true", help="sync to calendar provider if configured")
    p_sched_cal.add_argument("--provider", choices=("google", "caldav"), default=None)

    p_sched_cal.add_argument("--format", choices=("json", "ics"), default="json")
    # Case study generation
    for name in ("case-study", "generate-case-study"):
        p_cs = sub.add_parser(name, help="generate sanitized systems engineering case study")
        p_cs.add_argument("--event-id", default=None)
        p_cs.add_argument("--source", default=None)
        p_cs.add_argument("--require-verified", action="store_true", default=True)
        p_cs.add_argument("--allow-unverified", action="store_true", default=False)
        p_cs.add_argument("--format", choices=("markdown", "json", "summary", "text"), default="markdown")
        p_cs.add_argument("--output", type=Path, default=None)
        p_cs.set_defaults(func=_cmd_generate_case_study)

    # Spaced repetition & active recall quiz
    for name in ("quiz", "review-quiz"):
        p_quiz = sub.add_parser(name, help="generate or conduct active-recall review quiz")
        p_quiz.add_argument("--course-id", default=None)
        p_quiz.add_argument("--limit", type=int, default=5)
        p_quiz.add_argument("--interactive", action="store_true")
        p_quiz.add_argument("--answers-json", default=None)
        p_quiz.add_argument("--no-remediation", action="store_true")
        p_quiz.set_defaults(func=_cmd_review_quiz)

    # Learning dashboard & progress analytics
    for name in ("dashboard", "analytics", "report"):
        p_dash = sub.add_parser(name, help="display learning progress dashboard & analytics")
        p_dash.add_argument("--course-id", default=None)
        p_dash.add_argument("--format", choices=("text", "json"), default="text")
        p_dash.add_argument("--output", type=Path, default=None)
        p_dash.set_defaults(func=_cmd_dashboard)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
