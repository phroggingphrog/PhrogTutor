# Learning-agent map: Academia concept -> pi-tutor (2026-10-18)
Owner: "map with my own learning agent concept from archival". Personalised learning runs in the backend only, no UI (owner 2026-10-18).

## Sources (all read, nothing copied into prompts)
- Concept + settled rules: ~/archive/workspace-20261004/hermes-backend-Academia/ACADEMIA_IMPLEMENTATION_HANDOVER.md, ACADEMIA_FIRST_SLICE_BUILD_REPORT.md
- Contract: ~/brain/data/legacy/hermes/project_docs/core_conceptual_design/04_AGENT_CONCEPTS/INTEGRATED_LEARNING_PROGRESS_TRACKING.md
- Policy json: ~/brain/data/legacy/hermes/learning/hermes-learning-integration-v1.json
- Code (8,021 lines, 39 tests passed): ~/archive/workspace-20261004/hermes-tools/learning-tracking/learning_tracking/ (+ academia_learning/)
- Live store: ~/brain/stores/learning/learning.sqlite3 (608 chunks, 8 attempts/assessments/evaluations/decisions, learning_event 0, review_event 0, error_ledger 0)

## Settled rules to keep (do not reopen)
1. start learning = zero choice: ONE block with a plain-language reason, never a menu.
2. Selection order: nearest protected deadline (canvas_due - 7 days) -> missing prerequisite -> lowest demonstrated confidence -> due spaced-retrieval item.
3. Evidence is append-only: attempt -> assessment -> evaluation proposal -> confirmation. A model can never confirm its own evaluation (only user-provided / deterministic check / instructor feedback).
4. Reading or chatting never creates mastery. Unknown confidence stays None, never 0.
5. Progress states NOT_STARTED..VERIFIED, mastery tracked per dimension, no single percentage.
6. Every cited claim carries source id/version, record_ref, sha256, byte range; missing evidence = "insufficient evidence".
7. No assessed work generated or submitted for the owner. No second learning DB.

## Concept part -> where it lives in pi-tutor
| Academia part | Today | In pi-tutor |
|---|---|---|
| Orchestrator + capabilities (chat, course_study, mastery...) | deeptutor/runtime, capabilities, agents | ONE pi SDK session per turn (as in pi-study); same StreamEvents so the UI is unchanged |
| RAG (3 namespaces: UNIVERSITY_COURSE, LEARNER_EVIDENCE, MAIN_HERMES) | llama-index + faiss | wiki qmd MCP as pi tools (university + learner evidence); MAIN_HERMES stays unimplemented |
| Citations (sha256 + byte range) | none | citations.py from learning_tracking, called by the wiki `get` tool wrapper |
| Selector (4-stage cascade) | course_study capability prompt | selection.py + deadlines.py + spaced_retrieval.py as a pi tool `next_block` (code decides, model explains) |
| Evidence chain + mastery + confidence | DeepTutor mastery.sqlite3 | learning.sqlite3 schema (attempt/assessment/evaluation/decision/learning_event/review_event/error_ledger) via evidence.py + mastery.py |
| Data collection (the new part) | none | after each turn a backend hook writes one learning_event (course, topic, question, answer ref, tool calls, time) and, for quizzes, an attempt; no UI |
| Profile read-back | memory | learner_profile.py builds a short per-topic profile that is injected into the next turn's system prompt |

## Build order (each proven before the next)
A. Copy learning_tracking into pi-tutor as `pi_learning/` (reuse, do not rewrite), point it at a COPY of learning.sqlite3, run its 39 tests.
B. pi SDK turn engine behind the existing chat route; tools: wiki query/get, canvas, next_block, record_attempt.
C. Backend hook: learning_event per turn + profile injection. Check with one real turn and read the rows back.
D. Replace llama-index/faiss, then show the owner the dead-code/deps list before deleting anything.

## Gaps the archive itself lists (to close, not redesign)
No FSRS/review_event writer is live (review_event = 0); learning_event and error_ledger are empty; no live path from a chat turn to the evidence chain. Steps B and C close these.

## Status
- A done (2026-10-18, commit 6851ec7): `pi_learning/` = the archive copy of `learning_tracking/` + `academia_learning/` (no rewrite). Two paths repointed because the old ones are gone: the policy json is now `pi_learning/policy/hermes-learning-integration-v1.json` (`learning_event.DEFAULT_POLICY_PATH`), and the DB is `PI_LEARNING_DB` env or `~/brain/stores/learning/learning.sqlite3` (`db.DEFAULT_DB_PATH`). Tests: 200 passed (`uv venv /tmp/pl/venv`, pytest, `PI_LEARNING_DB=/tmp/pl/learning.sqlite3` — a copy, the live file untouched; the suite itself also builds its own temp DB from a copied schema).
- B done (2026-10-18, commit 0f759b7): `pi_engine/turn.ts` (bun, pi SDK) = one `createAgentSession` per turn, tools = wiki query/get + read-only Canvas. `deeptutor/runtime/pi_turn_engine.py` runs it as a subprocess and emits the orchestrator's StreamEvents; `deeptutor/runtime/turn_engine.py` uses it only when `PI_TUTOR_ENGINE=1`. Proof: `PYTHONPATH=. PI_TUTOR_ENGINE=1 python3 scripts/pi_one_turn.py "<question>"` produced session → tool_call (llm_wiki__query) → tool_result → tool_call (llm_wiki__get) → tool_result → content* → done(completed) in 82 s.
- pi_engine config: `agent-dir/mcp.json` + `settings.json` are committed; `agent-dir/models.json` (holds the OmniRoute key) and the `node_modules` symlink to the global pi SDK are gitignored. Local files only — no new service.
- Undo: `git revert 0f759b7 6851ec7` (both are additive; unset `PI_TUTOR_ENGINE` and the old orchestrator path is used again).
- C next: after each turn write one `learning_event` (course, topic, question, answer, tool calls, time) with `pi_learning`'s `learning_event.py`, and inject `learner_profile.py`'s per-topic profile into the next turn's system prompt (the `system` field of the JSON the engine already accepts).
