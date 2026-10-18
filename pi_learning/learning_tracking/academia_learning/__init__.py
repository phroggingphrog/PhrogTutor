"""Academia-owned adapter over the existing native learning store.

This package binds the Academia profile's learning-agent role to the
already-existing, already-populated database at
``/home/phroggingphrog/data/hermes/db/learning.sqlite3`` (see
``db.DEFAULT_DB_PATH``). It does not create a new database, vector service,
or duplicate index.

Three namespaces are kept separate (see ``namespaces.py``):

- ``UNIVERSITY_COURSE``  -- course/, source_version/, chunk/, chunk_fts
- ``LEARNER_EVIDENCE``   -- objective_version/, prerequisite/, exercise_version/,
                            attempt/, assessment/, mastery_evaluation/,
                            mastery_decision/, review_event (new, additive)
- ``MAIN_HERMES``        -- explicitly out of scope for this package; callers
                            must go to baseline.db/canonical.db/project_docs
                            themselves. This package never touches them.

Ownership model: every write in this package is explicit and typed
(``append_attempt``, ``propose_evaluation``, ``confirm_evaluation``,
``record_review``). Nothing here runs as a silent, global, every-turn hook --
callers (Academia) invoke ``start_block``/``append_attempt`` deliberately, in
an explicit learning mode (``start learning`` / ``continue learning`` /
``review``), matching the ownership boundary this package is meant to
replace the old global ``native_learning.py`` cli.py hook with.
"""

from .api import (
    AcademiaLearningError,
    InsufficientEvidenceError,
    ValidationFailedError,
    append_attempt,
    confirm_evaluation,
    grade,
    learner_profile,
    mastery_status,
    propose_evaluation,
    read_progress,
    record_review,
    retrieve_evidence,
    start_block,
    record_learning_event,
    validate_learning_event,
    get_learning_event_by_id,
    record_error,
    list_errors,
    error_summary,
    schedule_error_remediation,
)
from .learning_event import LearningEvent, EpistemicClass, SourceSensitivity
from .curriculum import generate_learning_event_from_incident
from .path_allowlist import validate_evidence_path, is_safe_evidence_path
from .db import DEFAULT_DB_PATH
from .routing import classify_task_complexity
__all__ = [
    "AcademiaLearningError",
    "InsufficientEvidenceError",
    "ValidationFailedError",
    "start_block",
    "retrieve_evidence",
    "append_attempt",
    "read_progress",
    "record_review",
    "record_learning_event",
    "validate_learning_event",
    "get_learning_event_by_id",
    "record_error",
    "list_errors",
    "error_summary",
    "schedule_error_remediation",
    "LearningEvent",
    "EpistemicClass",
    "SourceSensitivity",
    "generate_learning_event_from_incident",
    "validate_evidence_path",
    "is_safe_evidence_path",
    "propose_evaluation",
    "confirm_evaluation",
    "mastery_status",
    "grade",
    "learner_profile",
    "DEFAULT_DB_PATH",
    "classify_task_complexity",
]
