import argparse
import json
import sys
from pathlib import Path

from .canvas import load_assignments
from .concepts import concept_terms
from .examples import find_example
from .planner import build_plan, rank_assignments
from .progress import derive_mastery


DEFAULT_CANVAS = Path(
    "/home/phroggingphrog/data/hermes/runtime/canvas_scan/course_materials/upcoming_assignments_summary.json"
)
DEFAULT_PROGRESS = Path(
    "/home/phroggingphrog/data/hermes/runtime/learning-tracking/progress.jsonl"
)
DEFAULT_EXAMPLE_ROOTS = [
    Path("/home/phroggingphrog/projects/hermes-data-system/worktrees/analytics-ops-v1/src"),
    Path("/home/phroggingphrog/projects/hermes-tools"),
    Path("/home/phroggingphrog/.hermes/hermes-agent"),
]

def main(argv: list[str] | None = None) -> int:
    # The installed ``learning-tracking`` console entry point owns this
    # dispatcher.  Keep the legacy assignment modes unchanged while exposing
    # the explicit incident workflow through Academia's CLI.
    dispatch_argv = sys.argv[1:] if argv is None else argv
    academia_cmds = {
        "record-incident",
        "plan-canvas",
        "record-error",
        "list-errors",
        "error-summary",
        "schedule-calendar",
        "mastery-status",
        "record-attempt",
        "resume",
        "ingest-source",
        "ingest-objective",
        "learner-profile",
        "case-study",
        "generate-case-study",
        "quiz",
        "review-quiz",
        "dashboard",
        "analytics",
        "report",
    }
    if any(cmd in dispatch_argv for cmd in academia_cmds):
        from .academia_learning.__main__ import main as academia_main

        return academia_main(dispatch_argv)

    parser = argparse.ArgumentParser(description="Build one project-aware learning block.")
    parser.add_argument(
        "mode", choices=("start-learning", "start-assignment", "start-brainstorm")
    )
    parser.add_argument("--canvas-summary", type=Path, default=DEFAULT_CANVAS)
    parser.add_argument("--progress", type=Path, default=DEFAULT_PROGRESS)
    parser.add_argument("--example-root", type=Path, action="append")
    parser.add_argument("--max-files", type=int, default=200)
    parser.add_argument("--max-bytes", type=int, default=1_000_000)
    args = parser.parse_args(argv)
    assignments = load_assignments(args.canvas_summary)
    if not assignments:
        raise SystemExit("Canvas summary contains no assignments")
    assignment = rank_assignments(assignments)[0]
    query = " ".join(concept_terms(assignment.course, assignment.title, assignment.prerequisites))
    example = find_example(
        query,
        args.example_root or DEFAULT_EXAMPLE_ROOTS,
        max_files=args.max_files,
        max_bytes=args.max_bytes,
    )
    output = build_plan(assignment, mode=args.mode, example=example)
    output["assignment"] = {
        "id": assignment.id,
        "title": assignment.title,
        "course": assignment.course,
        "due_at": assignment.due_at.isoformat() if assignment.due_at is not None else None,
        "personal_final": assignment.personal_final.isoformat() if assignment.personal_final is not None else None,
        "original_due_at": (
            assignment.original_due_at.isoformat() if assignment.original_due_at is not None else None
        ),
        "extended_due_at": (
            assignment.extended_due_at.isoformat() if assignment.extended_due_at is not None else None
        ),
    }
    output["mastery"] = derive_mastery(args.progress)
    print(json.dumps(output, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
