# Provenance: repository understanding integrations for pi-tutor

- **Date:** 2026-10-19
- **Rounds:** 1 evidence-gathering round; 1 lead citation/content-check and review pass. No independent verification round completed.
- **Sources consulted:** Three official repository pages fetched; three pinned upstream source checkouts; local pi-tutor source; search discovery via three exact queries. See research-direct.md for source paths and queries.
- **Sources accepted:** 27 selected pinned primary code/document/manifest/licence URLs fetched and compared byte-for-byte to inspected checkouts (27 matches), plus local source checks (18 text assertions found). These counts describe checks, not independent studies or behavioral tests.
- **Sources rejected:** Search snippets/old Graphify provider issue not used to establish current behavior; no unverified token prices/learning benchmarks included. GitHub API metadata failed HTTP 403 rate-limit; replaced with ordinary source cloning, not another agent execution protocol.
- **Verification:** BLOCKED
- **Reason:** Required delegated evidence/citation/review chain unavailable after provider 503 ALL_TARGETS_SKIPPED. Parent static checks completed, but cannot be described as an independent verifier/reviewer pass. Runtime correctness, installed size, latency, model usage/cost, browser/offline security and learning effects are NOT RUN.
- **Plan:** [outputs/.plans/understand-repos-brief.md](.plans/understand-repos-brief.md)
- **Draft:** [.drafts/understand-repos-brief-draft.md](.drafts/understand-repos-brief-draft.md)
- **Cited draft:** [.drafts/understand-repos-brief-cited.md](.drafts/understand-repos-brief-cited.md)
- **Research files:** [.drafts/understand-repos-brief-research-direct.md](.drafts/understand-repos-brief-research-direct.md)
- **Lead check record:** [.drafts/understand-repos-brief-verification.md](.drafts/understand-repos-brief-verification.md)
- **URL/content evidence:** [.drafts/understand-repos-brief-url-checks.json](.drafts/understand-repos-brief-url-checks.json)
- **Local source evidence:** [.drafts/understand-repos-brief-local-checks.json](.drafts/understand-repos-brief-local-checks.json)
- **Reproducible citation-fetch script:** [.drafts/understand-repos-brief-citation-check.py](.drafts/understand-repos-brief-citation-check.py). Note: rerunning regenerates cited draft; final formatting adjustments were applied separately.

## Source revisions

| Repository | Commit | Local snapshot |
|---|---|---|
| https://github.com/Egonex-AI/Understand-Anything | f2d0164f72fe678805c34245a6df36acededfebd | `.drafts/upstream-understand/` |
| https://github.com/tt-a1i/archify | 654da74e084e5a1faf708779c23d6172766be9c1 | `.drafts/upstream-archify/` |
| https://github.com/Graphify-Labs/graphify | 86c57be8204673c2a1b9849a3292a7dd3ab373d8 | `.drafts/upstream-graphify/` |
| Local DeepTutor fork | a2bd42a381db61d97915d87cbc984377fb61b438 | `/home/phroggingphrog/projects/pi-tutor` |

## Delegated outcomes and execution boundaries

Workflow `d27e2200-8983-4731-858a-0314fa7f45c4` stopped after infrastructure failure.

| Key | Child ID | ok / outcome | Output evidence |
|---|---|---|---|
| understand | 3855c03c-b9ff-47e7-b7fc-78c05d02b9a5 | false; 503 ALL_TARGETS_SKIPPED | `/home/phroggingphrog/.feynman/sessions/subagent-artifacts/3855c03c-b9ff-47e7-b7fc-78c05d02b9a5_researcher_output.md` (error only) |
| graphs | eab6fcaa-3acc-4213-8765-b05ef1e7ddb2 | false; same provider error | `/home/phroggingphrog/.feynman/sessions/subagent-artifacts/eab6fcaa-3acc-4213-8765-b05ef1e7ddb2_researcher_output.md` (error only) |
| draft-barrier | 55550376-1750-45b0-a3fa-0d42f6f9dd87 | stopped | no usable artifact; reply raced workflow stop |
| citations / review | not launched | blocked | lead-created cited/verification files explicitly not independent outputs |

Receipt: `/tmp/pi-subagents-uid-1000/async-subagent-runs/d27e2200-8983-4731-858a-0314fa7f45c4/workflow-receipt.json` (retention-managed). Worktree status capture: `.drafts/understand-repos-brief-worktree-status.txt`; initially clean, no application code modified. Research checkouts/artifacts only. No dependencies installed, upstream code executed, browser launched, paid inference requested, or student repository transmitted. This is a static research brief, not an implemented integration or security certification.
