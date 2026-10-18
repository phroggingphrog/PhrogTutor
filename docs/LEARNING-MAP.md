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

## Retrieval surface today (step D part 1, read-only map)

Counted on `pi-core` at `a8264bc`: **427 references to `llama_index`/`faiss` across 76 files**
(210 in production, 217 in tests; 44 production files). The retrieval surface is:

| # | Surface | What it is | Size |
|---|---|---|---|
| 1 | `deeptutor/services/rag/pipelines/llamaindex/` | The engine: `vector_store.py` (FAISS + `llama_index.core` storage), `pipeline.py` (the `search`/`initialize`/`add_documents`/`delete` contract), `ingestion.py`, `retrievers.py` (BM25 fusion), `rerank.py`, `embedding_adapter.py`, `storage.py`, `document_loader.py`, `config.py`, `errors.py`, `exercise_lookup.py` | 2,987 lines, 12 files |
| 2 | `deeptutor/services/rag/factory.py` | `DEFAULT_PROVIDER = "llamaindex"`, `_build_pipeline` branch, `provider_uses_embedding_versions` (only llamaindex, 5 refs) | 415 lines |
| 3 | `deeptutor/services/rag/index_probe.py` | `_inspect_llamaindex` + `_llamaindex_doc_count` readiness probe (5 refs) | 318 lines |
| 4 | `deeptutor/services/rag/index_versioning.py` + `embedding_signature.py` | Version dirs keyed by embedding signature; the `version-1/` layout is the llamaindex store | 4 + 1 refs |
| 5 | `deeptutor/logging/adapters/llamaindex.py` | Log forwarding adapter, exported by `logging/adapters/__init__.py` | 74 lines, 11 refs |
| 6 | `deeptutor/api/routers/knowledge.py` | `LlamaIndexConfigUpdate` + read/update routes for the engine knobs | 9 refs |
| 7 | `deeptutor/services/workspace/kb_move.py` | Rebases paths inside FAISS/llamaindex store files on KB move | 4 refs |
| 8 | `deeptutor/services/rag/linked_kb.py`, `provider_binding.py`, `agents/loop/pipeline.py`, `services/parsing/engines/*`, `services/embedding/*`, `capabilities/*` | Mostly comments/documentation mentioning llamaindex; loop pipeline has 3 real `#650` co-selection guards | 1–3 refs each |
| 9 | Tests | 25 files, 217 refs; `test_llamaindex_faiss_vector_store.py` (61) and `test_llamaindex_document_loader.py` (43) are the heaviest | — |

## Migration order (step D, decided here, not yet executed)

1. **Done (part 1):** the additive adapter behind the flag. `PI_TUTOR_RAG=wiki` makes the fallback
   provider `wiki`; llama-index/faiss stay the default and nothing is selected until the operator
   opts in.
2. **Prove at parity (part 2, first):** run the archive's own RAG eval (`deeptutor/services/rag/eval`)
   over the same questions through both providers and compare returns; a feature the wiki cannot
   serve (see the keep-list below) blocks the switch for that KB.
3. **Per-KB, not global:** bind one KB to `wiki` through `kb_config.json` (`rag_provider`) and run a
   real turn; explicit bindings always win over the flag, so this is reversible per KB.
4. **Flip the default only after the owner approves the removal list** (part 2, owner-gated).
5. **Removal (part 2, owner-approved):** delete the engine, then the routes, logging adapter, probe
   and version plumbing that exist only for it, then drop the dependencies. Only then does
   `PI_TUTOR_RAG` stop being a flag.

## Keep-list: features that must survive the switch (owner rule: "KEEP EVERY FEATURE")

The wiki provider is **retrieval-only**. These are the features the llamaindex engine carries that
the wiki cannot serve as-is, so they are the hard part of part 2, not part 1:

- **Ingestion** — `initialize`/`add_documents` on a KB (upload → parse → chunk → embed → index).
  `WikiQmdPipeline` raises instead: markdown goes into the wiki and qmd indexes it. The upload UI
  still needs a real path for non-wiki KBs.
- **Numbered exercise lookup** — `exercise_lookup.py` (373 lines) returns exact figures/tables for
  "exercise 3.2" style queries because tokenization discards chapter numbers. The wiki cannot do
  this today; either port it or accept the regression before removing.
- **Rerank + BM25 hybrid fusion** — `retrievers.py`/`rerank.py`; qmd does its own lex+vec fusion and
  rerank, so this is a quality comparison (step 2 above), not a mechanical port.
- **Visual assets / image nodes** — `visual_assets.py` refs, `ImageNode` handling in the loader for
  figures and captions; the wiki returns markdown text only.
- **Per-KB isolation and multi-user KBs** — one llama-index store per KB vs one shared qmd index;
  collections are the wiki's only scoping.
- **Embedding-model binding UI** — `embedding_binding.py` / version signatures.

## Dead code and dependency removal list (for the owner to approve — NOTHING removed yet)

Delete only after step 5 approval. Line counts are exact; reference counts are per file.

| Item | Path | Lines | Production refs |
|---|---|---|---|
| Engine package | `deeptutor/services/rag/pipelines/llamaindex/` (12 files) | 2,987 | 202 |
| Logging adapter | `deeptutor/logging/adapters/llamaindex.py` | 74 | 11 |
| Engine config routes | `deeptutor/api/routers/knowledge.py` (class + 2 routes) | — | 9 |
| Readiness probe | `deeptutor/services/rag/index_probe.py` (`_inspect_llamaindex`, doc counter) | — | 4 |
| KB-move store rewriting | `deeptutor/services/workspace/kb_move.py` | — | 4 |
| Factory branch | `deeptutor/services/rag/factory.py` (`DEFAULT_PROVIDER`, `_build_pipeline`, `provider_uses_embedding_versions`) | — | 5 |
| Loop co-selection guards | `deeptutor/agents/loop/pipeline.py` (3 × `#650`) | — | 3 |
| Version/store plumbing | `deeptutor/services/rag/index_versioning.py`, `embedding_signature.py`, `linked_kb.py` | — | 7 |
| Comment-only mentions | `services/parsing/engines/*`, `services/embedding/*`, `capabilities/*`, `knowledge/*` | — | ~20 |
| Tests that die with it | `test_llamaindex_*.py` (9 files) + 16 mixed files | — | 217 |

**Dependencies to drop (4 declarations × 3 surfaces + 1 more):** `llama-index>=0.14.12`,
`llama-index-retrievers-bm25>=0.7.1,<0.8.0`, `llama-index-vector-stores-faiss>=0.4.0,<1.0.0`,
`faiss-cpu` (two Python-version pins) in `pyproject.toml` `[project.dependencies]` **and** the
`cli` extra, `packaging/deeptutor-cli/pyproject.toml`, and `requirements/cli.txt`.
`tests/test_packaging_metadata.py::test_python_314_rag_dependency_guards_match_every_install_surface`
asserts those declarations in all four places, so it must change with them (that is a packaging
test, not a RAG test).

Measured footprint of the pair today: the test venv built from `requirements/server.txt` is
**710 MB on disk** with llama-index + faiss installed. `faiss-cpu` is not a transitive of anything
else; `llama-index` is the root of a ~10-package tree.

**Not in scope for the removal:** `numpy`, `transformers`/`sentence-transformers` (the wiki's
EmbeddingGemma lives in qmd, not here), and anything the embedding service or parsers use
independently of llama-index.

## Namespace mapping decision (part 1)

The card names two retrieval namespaces ("university-course", "learner-evidence"); qmd has **no
collections by those names**. Its real collections are `log, phrogvps, inbox, university, me,
personal-learning, threads, projects, cheatsheets`. So:

- `university-course` → **`university`** (1,179 docs: course pages, concepts, raw source material)
- `learner-evidence` → **`personal-learning`** (6,204 docs)
- `MAIN_HERMES` stays unimplemented, as the map above already says.

The mapping lives in one dict (`NAMESPACE_COLLECTIONS` in `pipelines/wiki/client.py`) and
`PI_TUTOR_WIKI_COLLECTIONS` overrides it without a code change. The two names are kept as the
adapter's vocabulary so step D part 2 can bind either one per KB.

## Status
- A done (2026-10-18, commit 6851ec7): `pi_learning/` = the archive copy of `learning_tracking/` + `academia_learning/` (no rewrite). Two paths repointed because the old ones are gone: the policy json is now `pi_learning/policy/hermes-learning-integration-v1.json` (`learning_event.DEFAULT_POLICY_PATH`), and the DB is `PI_LEARNING_DB` env or `~/brain/stores/learning/learning.sqlite3` (`db.DEFAULT_DB_PATH`). Tests: 200 passed (`uv venv /tmp/pl/venv`, pytest, `PI_LEARNING_DB=/tmp/pl/learning.sqlite3` — a copy, the live file untouched; the suite itself also builds its own temp DB from a copied schema).
- B done (2026-10-18, commit 0f759b7): `pi_engine/turn.ts` (bun, pi SDK) = one `createAgentSession` per turn, tools = wiki query/get + read-only Canvas. `deeptutor/runtime/pi_turn_engine.py` runs it as a subprocess and emits the orchestrator's StreamEvents; `deeptutor/runtime/turn_engine.py` uses it only when `PI_TUTOR_ENGINE=1`. Proof: `PYTHONPATH=. PI_TUTOR_ENGINE=1 python3 scripts/pi_one_turn.py "<question>"` produced session → tool_call (llm_wiki__query) → tool_result → tool_call (llm_wiki__get) → tool_result → content* → done(completed) in 82 s.
- pi_engine config: `agent-dir/mcp.json` + `settings.json` are committed; `agent-dir/models.json` (holds the OmniRoute key) and the `node_modules` symlink to the global pi SDK are gitignored. Local files only — no new service.
- Undo: `git revert 0f759b7 6851ec7` (both are additive; unset `PI_TUTOR_ENGINE` and the old orchestrator path is used again).
- C done (2026-10-18): `deeptutor/runtime/pi_learning_hook.py` reads the seeded learner profile + confirmed per-objective states and builds the system-prompt block; `pi_turn_engine.py` injects it before the turn and appends ONE `learning_event` (course, topic, question, answer, tool calls, time, `mastery=pending`) after it through `pi_learning`'s `api.record_learning_event`. Both are best-effort: a failure logs a warning and the turn continues. The course is resolved from the owner's words/history, then from the wiki paths the tools returned (a query-only turn still lands on `bayesian-statistics`); a question with no course falls back to the last logged turn's course.
- Proof (all on copies — `PI_LEARNING_DB=/tmp/pl/learning.c.sqlite3`, live store untouched with 0 rows): one real turn quoted the injected profile verbatim with no tool calls ("direct conversational responses; visual explanations when useful; problem-based and Socratic practice…" + "visible structure; smaller chunks; user-controlled pacing; well-timed hints"), and three real turns wrote 4 `learning_event` rows read back with `sqlite3` (subsystem `bayesian-statistics`, tags `["bayesian-statistics", "Bayes' theorem…"]`, `mastery=pending`).
- Dependency: the reused package imports `fsrs` (spaced retrieval), so `fsrs>=6.0,<7` is declared in `pyproject.toml` (server extra) and `requirements/server.txt`; installed for the host python with `pip install --user --break-system-packages`.
- Still open from the archive's own gap list: no `review_event`/FSRS writer, `error_ledger` empty, quiz attempts not yet written from a chat turn, and nothing has been written to the LIVE store yet (enable `PI_TUTOR_ENGINE=1` in the service to start real collection).
- D part 1 done (2026-10-18): retrieval surface mapped (above); the additive wiki provider is in
  `deeptutor/services/rag/pipelines/wiki/` — `client.py` (265 lines, bounded MCP-over-HTTP client for
  the same qmd server `pi_engine/turn.ts` reaches as `llm-wiki`) + `pipeline.py` (171 lines, the
  `RAGPipeline` contract: `search` for real, `initialize`/`add_documents` raise, `delete` is a no-op).
  Wired additively into `factory.py` (`WIKI_PROVIDER`, `_build_pipeline` branch, `default_provider()`,
  `env_selected_provider()`, `list_pipelines` entry) and `provider_binding.py` (the fallback is
  `default_provider()`, so an explicit per-KB binding always wins). Nothing deleted; llama-index/faiss
  remain the default with `PI_TUTOR_RAG` unset. qmd's MCP is stateless (no session id) and answers
  plain JSON, so no handshake is needed; the client also parses SSE-framed bodies.
- D part 1 proof: `PYTHONPATH=. PI_TUTOR_RAG=wiki python3 scripts/pi_wiki_rag_query.py "Bayes theorem posterior with a beta prior"`
  → `default_provider() = wiki`, `collections: ['university', 'personal-learning']`, 5 sources,
  3 hydrated notes, `error_type: none`, top hit `university/raw/notebooklm/bayesian-statistics-master-rag-guide-v2.md`
  at 0.86. qmd is warm-fast on `lex` (<1 s) and slow on the first `vec` call of a session (~70 s while
  the embedding model loads), so the client's read timeout is 300 s by default.
- D part 1 tests (existing RAG tests, run unchanged): copied `tests/services/rag` to `/tmp/ragcopies`
  with a SHA-256 manifest (`sha256sum -c` verified after the runs) and ran the copies against the repo's
  `deeptutor` package in a throwaway venv (`uv venv /tmp/pi-test-venv` + `requirements/server.txt`, 710 MB).
  **699 passed, 12 failed, 48 skipped** — and the 12 failures are byte-identical to a baseline run of the
  same copies against a clean `git worktree` at `a8264bc` (`/tmp/pi-baseline`): 6 × `test_embedding_binding.py`
  and 3 × `test_graphrag_pipeline.py` (the graphrag/lightrag and embedding-binding suites expect extras and
  a model probe). So part 1 changes no existing test result. Suite at the default provider:
  `cd /tmp/ragcopies && PYTHONPATH=<repo> /tmp/pi-test-venv/bin/python -m pytest tests/services/rag -q`.
- D part 1 flag-on note: with `PI_TUTOR_RAG=wiki` the suite does not complete — KBs with no explicit
  binding now resolve to the wiki pipeline, and the engine-config/preflight tests then reach the live qmd
  server and block. That is the expected blast radius of flipping the fallback, not a part 1 regression;
  part 2 binds one KB at a time and must gate the config routes before the default flips.
- D part 2 (left for the owner-approved step): the actual removal, following the migration order and the
  keep-list above — parity run of `services/rag/eval`, per-KB binding through `kb_config.json`, and only
  then the engine/route/adapter deletion plus the four dependency declarations across their four surfaces.
- Undo for part 1: `git revert <part-1 commit>`; or simply leave `PI_TUTOR_RAG` unset — the added code is
  inert and llama-index/faiss stay the default.
