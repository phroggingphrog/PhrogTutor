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
