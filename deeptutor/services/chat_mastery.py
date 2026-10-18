"""Learn the learner's mastery from ordinary chat (TASK-71.4), without quizzing them.

1. Evidence: after each completed chat turn, one background model call reads the exchange and writes
   rows (concept, kind, quote, turn id) to ``chat_mastery.sqlite3``. It never delays the reply.
2. Mastery: one decaying Beta number per concept, rebuilt from the evidence rows every time it is read
   (half-life 30 days). Delete or edit a row and the number follows.
3. Scoped retrieval: only the 3-4 weakest concepts relevant to the new message go into the tutor prompt.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import sqlite3
import time
from pathlib import Path

logger = logging.getLogger(__name__)

# evidence kinds -> (success, failure) weight
KINDS = {
    "explained_correctly": (1.0, 0.0),
    "applied_correctly": (1.0, 0.0),
    "self_correction": (0.7, 0.3),
    "misconception": (0.0, 1.0),
    "hint_seeking": (0.0, 0.5),
    "confusion": (0.0, 0.7),
}
_HALF_LIFE_DAYS = 30.0
_TASKS: set[asyncio.Task] = set()

_SYSTEM = (
    "You watch one tutoring exchange and record what it shows about the LEARNER's understanding "
    "(not the tutor's). Reply with JSON only: {\"evidence\": [{\"concept\": str, \"kind\": str, \"quote\": str}]}.\n"
    "- concept: a short specific topic name, lowercase, 1-5 words (e.g. \"bayes theorem\", \"foreign keys\").\n"
    f"- kind: one of {', '.join(KINDS)}.\n"
    "- quote: the learner's own words (max 200 chars) that show it.\n"
    "Only academic or technical subject matter counts (maths, statistics, programming, databases, science...). "
    "Never record project planning, writing style, formatting, app usage or instructions as a concept.\n"
    "Only record what the learner's message itself shows. Asking for an explanation of something new is "
    "hint_seeking only if they are stuck on it. Small talk or no learning signal: {\"evidence\": []}."
)


def _db() -> sqlite3.Connection:
    from deeptutor.services.path_service import get_path_service

    path: Path = get_path_service().user_data_dir / "chat_mastery.sqlite3"
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path)
    con.execute(
        "CREATE TABLE IF NOT EXISTS evidence (id INTEGER PRIMARY KEY, ts REAL, session_id TEXT, turn_id TEXT,"
        " concept TEXT, kind TEXT, quote TEXT)"
    )
    return con


async def _extract(session_id: str, turn_id: str, user_message: str, answer: str) -> None:
    from deeptutor.services.llm import complete

    with _db() as con:
        known = [r[0] for r in con.execute("SELECT DISTINCT concept FROM evidence ORDER BY ts DESC LIMIT 80")]
    reuse = f"Concepts already tracked (reuse a name when it is the same idea): {', '.join(known)}\n\n" if known else ""
    prompt = reuse + f"LEARNER:\n{user_message[:4000]}\n\nTUTOR:\n{answer[:3000]}"
    raw = await asyncio.wait_for(
        complete(prompt=prompt, system_prompt=_SYSTEM, temperature=0, max_tokens=400, reasoning_effort="none"),
        timeout=60,
    )
    match = re.search(r"\{.*\}", raw or "", re.S)
    rows = (json.loads(match.group(0)).get("evidence") if match else None) or []
    now = time.time()
    clean = [
        (now, session_id, turn_id, str(r["concept"]).strip().lower()[:60], r["kind"], str(r.get("quote", ""))[:200])
        for r in rows
        if isinstance(r, dict) and r.get("kind") in KINDS and str(r.get("concept") or "").strip()
    ]
    if clean:
        with _db() as con:
            con.executemany(
                "INSERT INTO evidence (ts, session_id, turn_id, concept, kind, quote) VALUES (?,?,?,?,?,?)", clean
            )
    logger.info("chat mastery: turn %s -> %d evidence rows", turn_id, len(clean))


def record_turn(session_id: str, turn_id: str, user_message: str, answer: str) -> None:
    """Fire-and-forget: schedule the evidence extraction for one finished turn."""
    if not user_message.strip() or not answer.strip():
        return

    async def run() -> None:
        try:
            await _extract(session_id, turn_id, user_message, answer)
        except Exception:
            logger.warning("chat mastery extraction failed for turn %s", turn_id, exc_info=True)

    task = asyncio.create_task(run())
    _TASKS.add(task)
    task.add_done_callback(_TASKS.discard)


def mastery() -> list[dict]:
    """Every concept with its decaying Beta mean (0-1) and evidence count, weakest first."""
    with _db() as con:
        rows = con.execute("SELECT ts, concept, kind FROM evidence").fetchall()
    now, acc = time.time(), {}
    for ts, concept, kind in rows:
        w = 0.5 ** ((now - ts) / 86400 / _HALF_LIFE_DAYS)
        s, f = KINDS.get(kind, (0, 0))
        a, b, n = acc.get(concept, (1.0, 1.0, 0))
        acc[concept] = (a + w * s, b + w * f, n + 1)
    out = [{"concept": c, "mastery": round(a / (a + b), 2), "evidence": n} for c, (a, b, n) in acc.items()]
    return sorted(out, key=lambda r: r["mastery"])


# The learner's own rules (docs/learner-analysis-2026-10-19.md), always on.
_RULES = """How to teach this learner (always):
1. Open with a 1-2 sentence plain answer, then one small worked example with real numbers; add only what the concept method below asks for.
2. Explain why each step follows before any formula or syntax; never answer "look it up".
3. If they say they still don't understand, switch representation (numbers, a picture, their own code); do not reword the same explanation.
4. When they share their own answer or code, walk through it line by line before showing a model solution.
5. At most one meaning-based check question; never "do you understand?", never supervisory phrases like "Your turn" or "Try it yourself", no praise before checking a claim.
6. Short replies and keyword fragments are complete answers; let them set the pace.
7. Use their course notation (explicit H0/Ha, an HDI probability sentence for each Bayesian parameter, ER multiplicity at the opposite end); label invented numbers as hypothetical.
8. For graded work, make them say why each choice was made and what each output shows.
9. Stay short, finish what was asked, note side detours instead of following them.
10. Never mention mastery scores or turn the chat into a quiz."""

# Method by mastery band, rewritten in our own words from worked-example fading, Socratic questioning and retrieval practice.
_METHODS = (
    (0.45, "weak", "worked example then fading: show one fully worked example step by step, then end with a near-identical second example where every step is given except the last, phrased as an open question"),
    (0.70, "middle", "Socratic: give the key idea briefly, then ask ONE question that makes them take the next step themselves"),
    (1.01, "strong", "retrieval challenge: answer briefly, then pose one slightly harder twist for them to try from memory"),
)


def _method(score: float) -> tuple[str, str]:
    return next((band, how) for limit, band, how in _METHODS if score < limit)


def prompt_block(user_message: str, limit: int = 4) -> str:
    """The learner's teaching rules, plus a method for each tracked concept relevant to the message."""
    words = {w for w in re.findall(r"[a-z0-9]+", user_message.lower()) if len(w) > 2}
    hits = [m for m in mastery() if words & set(re.findall(r"[a-z0-9]+", m["concept"]))][:limit]
    practice = bool(re.search(r"\b(practi[cs]e|quiz|exercises?|questions?|test me|drill)\b", user_message, re.I))
    if practice and not hits:  # "give me practice" with no topic: practise the weakest concepts
        hits = mastery()[:3]
    if not hits:
        return _RULES
    lines = []
    for m in hits:
        band, how = _method(m["mastery"])
        lines.append(f"- {m['concept']} ({band}, from {m['evidence']} past chat signals): {how}")
    extra = (
        "\nThey asked for practice: make the questions about these concepts, easiest for weak ones, "
        "a harder twist for strong ones."
        if practice else ""
    )
    return _RULES + "\n\nLearned from earlier chats, for the concepts in this message use:\n" + "\n".join(lines) + extra
