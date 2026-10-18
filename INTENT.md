# pi-tutor: owner intent (verbatim)
2026-10-18: "fork the deep tutor -> replace it with pi agent sdk -> integrate our own rag system and the personalised learning with data collection"
2026-10-18: "debloat deep tutor, and replace the agent with PI AGENT SDK to make it lighter"
2026-10-16: "we are basically making a Deep Tutor clone", "pi agent sdk thats why we rewrite it", "wiki is local for everything, use the whole pi architecture instead of the deep tutor"
Note: "no need study plan bcs my uni already over" (2026-10-18) only drops exam-prep steps. It does not cancel this project.

## Plan (smallest steps, each one proven before the next)
1. Fork (done): ~/projects/pi-tutor, branch pi-core, from DeepTutor v1.6.13-13. Upstream remote renamed deeptutor-upstream, so nothing pushes to HKUDS.
2. Keep EVERY feature (owner 2026-10-18: "no dont do that partner co writer video learning reading book multi user visualiser textbook plugins and extra logins provider is important"). Debloat, in the owner's words: "make the code lighter and use a lighter agent alternative like pi agent sdk". So: no feature is deleted; the heavy agent code is swapped for the pi SDK (step 3), llama-index/faiss go when wiki search replaces them (step 4), and dead code/deps are trimmed after the owner sees the list. Measure before/after (lines, deps, RAM, startup).
3. Replace deeptutor/agents + capabilities + runtime with one pi SDK session per chat turn (as in ~/projects/pi-study/src/study.ts), behind the same /api/v1 chat route so the web UI keeps working.
4. RAG: drop llama-index/faiss; one search = the wiki qmd MCP (EmbeddingGemma, local), as pi tools.
5. Personalised learning + data collection (owner 2026-10-18: "the personalised learning which is automated behind the code not in the gui/ui"). Runs in the backend only, no new page, button or setting. After every turn the code itself logs (course, topic, question, answer, quiz result, time) to one SQLite file and updates a per-topic mastery profile; the next turn reads the profile into the system prompt on its own. The existing UI is left as it is. Data goes to the Acha dataset only where the owner says so.

See docs/LEARNING-MAP.md for how the owner Academia learning-agent concept (archive) maps onto steps 3-5.

## Scope reset (owner 2026-10-18, verbatim)
"it basically just porting deep tutor into lighter sdk" / "why it even asking study plan etc thats drifted way too far"
"the ideas should be deep tutor rewritten in pi can measured, and self collected the learner mastery based on the ai chat conversations instead of directly question it"

So, the whole project is two things:
1. DeepTutor's agent core runs on the pi agent SDK; every page and feature stays.
2. Mastery is measured from the normal chat conversations, collected by the backend on its own. No quizzes, no direct questions to the learner, no new UI. This REPLACES the Academia rule "reading or chatting never creates mastery" (deeptutor/runtime/pi_learning_hook.py): chat IS the evidence.

Out of scope: study plans, exam prep, deadline selector, spaced review, next_block, Canvas, quiz-based mastery. The Academia code (pi_learning/, the learning hook, docs/LEARNING-MAP.md) was REMOVED 2026-10-18 (owner: "remove the old acadmeia design then").
Focus now (owner 2026-10-18: "so lets focus on the port of deep tutor into pi agaent sdk first"): the port only. pi engine on by default + every page checked. Chat-based mastery comes after, as a new small hook.

## What "port" means (owner 2026-10-18, verbatim)
"making the pi as the SDK for the deep tutor that what i mean"
So pi is the agent SDK UNDER every DeepTutor agent and mode (chat, solve, research, question, visualize, co-writer, book, partners ...), with DeepTutor's own prompts and tools. Not a separate pi chat path with its own prompt. The seam is DeepTutor's model/agent layer: deeptutor/agents/base_agent.py (call_llm, stream_llm) and the agent loop in deeptutor/agents/loop, which every mode already goes through. The step-B pi_engine/turn.ts + TUTOR.md (uni-only prompt that replaces chat) is NOT the target; it is replaced by this.

## Full pi environment (owner 2026-10-18, verbatim)
"My original plan is to fully adapt it into pi-agent environment"
So the target is DeepTutor's agent loop running as a pi agent session (pi-agent-core), with DeepTutor's tools as pi tools, so pi extensions/plugins can load. The model-layer bridge (pi_engine/llm.ts) was step 1 of this, not the end.

## Differentiator (owner 2026-10-18, verbatim)
"this is what my fork will differentiate from deep tutor but i dont want a curriculum planner, but want a self learning agent on the user behavior"
Approved plan and order: board card TASK-71.4 (starts after the pi port is finished).
