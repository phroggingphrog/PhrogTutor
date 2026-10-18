# Implementation

The V1 flow is intentionally linear:

1. `canvas.load_assignments` reads the configured JSON. Canvas `due_at` already
   includes the user's +14-day accommodation; derive `original_due_at = due_at - 14
   days` and set `personal_final = original_due_at`; scheduling targets
   `personal_final`; do not add another 14 days.
2. `planner.rank_assignments` sorts by overdue/earliest personal deadline,
   prerequisite count, progress, and ID.
3. `concepts.concept_terms` selects a deterministic course profile, tolerates a
   trailing Canvas course code, appends non-generic assignment-title terms, and
   falls back to prerequisites for unknown courses.
4. `examples.find_example` searches only caller-provided roots within global
   file and byte budgets. Each distinct query or relevant path term contributes
   once, repeated generic wording contributes nothing, and equal scores use the
   lexically earliest path. It skips sensitive/generated path names, paths
   resolving outside a root, non-UTF-8 data, and NUL-containing files. The
   500-character excerpt is centered on the first strongest concept line.
5. `planner.build_plan` emits one learning block using the course profile and
   names the selected example purpose in its problem and artifact check.
   Brainstorm mode cannot execute.
6. `progress.append_attempt` appends JSONL; `derive_mastery` merges only explicit
   concept/dimension evidence.
7. `__main__` searches the analytics worktree, Hermes tools, then generic Hermes
   agent code and prints JSON.

The implementation uses the Python standard library. Pytest is used only for
tests. No default runtime input is accessed during import, and no secret file is
eligible for example retrieval.
