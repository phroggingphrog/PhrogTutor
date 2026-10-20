# Learning from how you learn and use AI: what the research says (2026-10-20)

Source: Consensus paper search (3 queries, top 20 each). Papers cited by number below.

## Questions
1. Can a tutor build a model of one learner from their chat history?
2. How do people use AI for brainstorming and problem solving, and what helps or hurts learning?

## Findings

**1. Chat history can track what you know, but an LLM alone does it badly.**
- An LLM can label each chat turn with the skill involved and whether the answer was right, and that beats classic tracking on dialogues (Scarlatos 2024, DOI 10.1145/3706468.3706501; Huang 2026, DOI 10.18653/v1/2026.bea-1.43).
- Used alone, LLMs give unstable and sometimes wrong-direction scores. A plain tracking model beat a fine-tuned LLM (AUC 0.83 vs about 0.77), so the LLM should read the chat and a separate score should do the tracking (Hooshyar 2026, DOI 10.1109/access.2026.3701047; Lee 2026, DOI 10.48550/arxiv.2601.01708).
- A tutor that separates deciding what to do (rules plus a Bayesian score) from the wording, with rules like attempt-before-hint, was preferred by experts 92% and avoided "mastery gain" inflated by over-helping (Kadir 2026, DOI 10.1007/978-3-032-29744-0_7).
- Summarising a learner's history into a profile before predicting their next turn beats using the current chat only (Duan 2026, DOI 10.48550/arxiv.2605.30051).
- Confusion and curiosity are common while solving problems, and neutral moments are where a tutor can steer (Zhang 2025, DOI 10.48550/arxiv.2510.13862).

**2. How you use AI matters more than how much.**
- Using AI to build and extend knowledge gives higher learning than using it to get a procedure done (Pallant 2025, DOI 10.1080/03075079.2025.2487570).
- Heavy reliance gave fluent but less original work. Selective use (idea start, language polish) kept critical reasoning and ownership (Tekir 2026, DOI 10.1080/09588221.2026.2617399).
- Learners fall into patterns: mastery-oriented people use AI to execute and evaluate, performance-oriented people to decide early, low-goal people to do their monitoring and reflection, which risks dependence (Wang 2026, DOI 10.1111/bjet.70059).
- Frequency of use mattered less than when and how it was used (same study).
- Almost everyone leans on AI most at the idea-starting stage. Studies suggest delaying AI during idea generation and using it as a critic at the converging stage (Lee 2026, DOI 10.36358/jce.2026.26.2.19).
- Critical thinking goes with reflective, cautious use. Trust alone predicts thoughtless use (Hou 2025, DOI 10.1111/bjet.13613). A critical-thinking prompt cut copying AI output and made solutions more creative (Hou 2026, DOI 10.1016/j.compedu.2026.105576).
- Gen-AI has a moderate positive effect on higher-order thinking, strongest for problem solving, and it works better over 8 to 16 weeks and for people who self-regulate well (Zhao 2025, DOI 10.3390/jintelligence13120160).
- Offloading is not one thing: dependent and autonomous offloading differ (Zhu 2026, Frontiers in Psychology).

## Second round (2026-10-20): how to build critical reasoning, and AI as a discussion partner
- A Socratic tutor made students better at using AI on their own later, even though they found it slower (Brender 2026, DOI 10.1007/978-3-032-29763-1_37).
- Problem-solving before instruction has a moderate benefit over 53 studies (g 0.36, higher with high-fidelity productive failure) (Sinha 2021, DOI 10.3102/00346543211019105). Unassisted discovery does not help; feedback, worked examples and scaffolding do (Alfieri 2011, DOI 10.1037/a0021017).
- Forcing a pause before showing the AI answer reduces blind acceptance but people rate it worst (Buçinca-style cognitive forcing, 2021, DOI 10.1145/3449287). An assumption-check prompt cut over-reliance without extra load (Ghosh 2026, DOI 10.48550/arxiv.2601.18033).
- Help-seeking differs by person: top students ask for ideas and checks, weaker ones delegate the whole task; the AI mirrors the student's intent (Rinja 2026, DOI 10.1007/978-3-032-29763-1_35).
- Support should rise when the learner fails and fade when they take it up (Hou 2026, DOI 10.48550/arxiv.2609.22993). Rules such as attempt-before-hint work best enforced by code with the LLM only wording them (Kadir 2026, DOI 10.1007/978-3-032-29744-0_7).
- Risks: Socratic-sounding questions can mimic reasoning (Sánchez 2026, DOI 10.2196/102958), and tracking can make people perform instead of think, so what the tutor learns must be visible and deletable.

## What was built from it
- **Usage style** (not mastery): each chat turn gets one label (tried_first, asked_for_answer, pushed_back, accepted, own_idea) in the same background call; shown and deletable in Settings, Conversation. The tutor adapts once one style reaches 40% of at least 8 signals. Kept apart from the mastery page on purpose.
- **Discussion modes** in the composer: Think-first (you try, then it helps), Critic (it argues the other side), Teach-back (you explain, it plays a novice). The mode block goes last in the prompt so it overrides the general teaching rules. A short assumption or what-if check runs only while a mode is on.
- **Baseline method** (private, not in this repo): a sample of the owner's short typed messages from ChatGPT, Claude and Gemini exports labelled with the same five labels by Gemini Flash.
- **Not built:** a code-enforced hint cap (attempt-before-hint as a hard rule), and a before/after comparison over two weeks of live use.

## What this means for PhrogTutor (already built vs next)
| Finding | Status in PhrogTutor |
|---|---|
| LLM reads chats, a separate decaying score tracks mastery | Built (extractor + Beta score) |
| Judge reasoning, not recited wording | Built |
| Profile built from your history before teaching | Built (mastery block, understood pages) |
| Rules like attempt-before-hint | Not built: tutor rules exist, but no hard rule that makes you try first |
| Learn your *style of using AI* (when you ask for help, whether you edit the answer or paste it) | Not built |
| Delay AI at idea-starting, act as critic when converging (brainstorm mode) | Not built |
| Spot thoughtless use, ask for your own reasoning first | Not built |
| Time-based test: does a learned pattern still hold after weeks | Not built |

## Suggested next builds (smallest first)
1. Record one extra signal per chat turn: did you answer the tutor's question first, edit its answer, or just accept it. Same background call, one more field.
2. In brainstorm chats, the tutor asks for your own first idea before giving any, then switches to critic.
3. Show the pattern on the mastery page ("you usually ask for help before trying") so you can see and delete it.

## Limits
Most studies are surveys or small classroom studies of students, not one adult learner over months. Treat the patterns as hypotheses to check against your own chats.
