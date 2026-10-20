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
