# Project-Aware Learning Planner V1

Builds one actionable learning block around the Canvas assignment with the
earliest personal deadline. Canvas `due_at` already includes the user's +14-day
accommodation; derive `original_due_at = due_at - 14 days`; set
`personal_final = original_due_at`; scheduling targets `personal_final`; do not add
another 14 days.

## Run

```bash
python -m learning_tracking start-learning
python -m learning_tracking start-assignment
python -m learning_tracking start-brainstorm
```

`start-brainstorm` returns `"execution_allowed": false`; the other modes allow
execution. Output is JSON containing one `block`, never a menu of options.

Runtime inputs and scan limits can be overridden:

```bash
python -m learning_tracking start-learning \
  --canvas-summary tests/fixtures/canvas.json \
  --progress /tmp/progress.jsonl \
  --example-root tests/fixtures/hermes \
  --max-files 100 \
  --max-bytes 500000
```

Repeat `--example-root` to allow more roots. Only those roots are searched.
`.env`, `.git`, auth, credential, cache, logs, raw, canonical, and binary files
are excluded. Retrieval caps repeated words, rewards distinct course-profile
matches and relevant path terms, and breaks ties by lexical path. Excerpts are
capped at 500 characters and centered on the strongest matching line.

Known course profiles cover Database Concepts, Analysis of Categorical Data,
and Applied Bayesian Statistics. Non-generic assignment-title terms extend the
profile; `assessment`, `assignment`, `case`, and `studies` do not affect search.

Defaults:

- Canvas: `/home/phroggingphrog/data/hermes/runtime/canvas_scan/course_materials/upcoming_assignments_summary.json`
- Progress: `/home/phroggingphrog/data/hermes/runtime/learning-tracking/progress.jsonl`
- Examples, in order: `/home/phroggingphrog/projects/hermes-data-system/worktrees/analytics-ops-v1/src`, `/home/phroggingphrog/projects/hermes-tools`, `/home/phroggingphrog/.hermes/hermes-agent`

## Progress evidence

Attempts are append-only JSONL. Mastery contains only dimensions explicitly
recorded under `evidence`:

```json
{"assignment_id":"essay","evidence":{"source analysis":{"recall":true}}}
```

## Test

```bash
python -m pytest -q
```
