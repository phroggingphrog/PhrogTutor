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
import os
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
# A cheap model for the background mastery calls (on PhrogVPS: the OmniRoute combo "deepseek-flash").
_EXTRACT_MODEL = os.environ.get("PI_TUTOR_EXTRACT_MODEL") or None  # None = the active chat model

# How the learner uses the AI (not what they know): tracked per turn, shown to them, used to adapt the tutor.
STYLES = ("tried_first", "asked_for_answer", "pushed_back", "accepted", "own_idea")

_SYSTEM = (
    "You watch one tutoring exchange and record what it shows about the LEARNER's understanding "
    "(not the tutor's). Reply with JSON only: {\"evidence\": [{\"concept\": str, \"kind\": str, \"quote\": str}], \"style\": str}.\n"
    "- concept: a short specific topic name, lowercase, 1-5 words (e.g. \"bayes theorem\", \"foreign keys\").\n"
    f"- kind: one of {', '.join(KINDS)}.\n"
    "- quote: the learner's own words (max 200 chars) that show it.\n"
    "Only academic or technical subject matter counts (maths, statistics, programming, databases, science...). "
    "Never record project planning, writing style, formatting, app usage or instructions as a concept.\n"
    "Judge reasoning, not wording: explained_correctly needs the learner's own why/how (a reason, a link "
    "between ideas, a new example or a prediction). Reciting a definition, correct jargon or the tutor's words "
    "back is NOT evidence. applied_correctly needs them to use it on a problem the tutor did not solve for them.\n"
    f"- style: how the learner used the AI in THIS message, one of {', '.join(STYLES)}, or \"none\" when unclear. "
    "tried_first = shares their own attempt, guess or idea before asking; asked_for_answer = wants the answer or "
    "code with no attempt of their own; pushed_back = questions, corrects or changes the tutor's answer; "
    "accepted = just takes the answer and moves on; own_idea = brings their own idea to build on.\n"
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
    if "answer" not in [r[1] for r in con.execute("PRAGMA table_info(evidence)")]:
        con.execute("ALTER TABLE evidence ADD COLUMN answer TEXT DEFAULT ''")  # the tutor reply it came from
    con.execute(
        "CREATE TABLE IF NOT EXISTS explanations (id INTEGER PRIMARY KEY, ts REAL, concept TEXT, outcome TEXT,"
        " tutor_answer TEXT, learner_quote TEXT)"
    )
    con.execute("CREATE TABLE IF NOT EXISTS usage_style (id INTEGER PRIMARY KEY, ts REAL, session_id TEXT, style TEXT, quote TEXT)")
    return con


async def _extract(session_id: str, turn_id: str, user_message: str, answer: str) -> None:
    from deeptutor.services.llm import complete

    with _db() as con:
        known = [r[0] for r in con.execute("SELECT DISTINCT concept FROM evidence ORDER BY ts DESC LIMIT 80")]
    reuse = f"Concepts already tracked (reuse a name when it is the same idea): {', '.join(known)}\n\n" if known else ""
    prompt = reuse + f"LEARNER:\n{user_message[:4000]}\n\nTUTOR:\n{answer[:3000]}"
    raw = await asyncio.wait_for(
        complete(prompt=prompt, system_prompt=_SYSTEM, model=_EXTRACT_MODEL, temperature=0, max_tokens=400, reasoning_effort="none"),
        timeout=60,
    )
    match = re.search(r"\{.*\}", raw or "", re.S)
    parsed = json.loads(match.group(0)) if match else {}
    rows = parsed.get("evidence") or []
    now = time.time()
    if parsed.get("style") in STYLES:
        with _db() as con:
            con.execute("INSERT INTO usage_style (ts, session_id, style, quote) VALUES (?,?,?,?)",
                        (now, session_id, parsed["style"], user_message[:200]))
    clean = [
        (now, session_id, turn_id, str(r["concept"]).strip().lower()[:60], r["kind"], str(r.get("quote", ""))[:200], answer[:2000])
        for r in rows
        if isinstance(r, dict) and r.get("kind") in KINDS and str(r.get("concept") or "").strip()
    ]
    if clean:
        with _db() as con:
            con.executemany(
                "INSERT INTO evidence (ts, session_id, turn_id, concept, kind, quote, answer) VALUES (?,?,?,?,?,?,?)",
                clean,
            )
        for row in clean:
            await _learn_from(row[3], row[4], row[5], row[1])
    logger.info("chat mastery: turn %s -> %d evidence rows", turn_id, len(clean))


_WEAK = ("misconception", "confusion", "hint_seeking")
_SUCCESS = ("applied_correctly", "explained_correctly")


def _rules_path() -> Path:
    from deeptutor.services.path_service import get_path_service

    return get_path_service().user_data_dir / "teaching_rules.md"


async def _learn_from(concept: str, kind: str, quote: str, session_id: str) -> None:
    """Log whether the tutor's last explanation of a weak concept worked, and turn a later independent
    success into one teaching rule (teaching_rules.md; delete a line to undo it)."""
    if kind not in _WEAK + _SUCCESS:
        return
    with _db() as con:
        prev = con.execute(
            "SELECT answer, quote FROM evidence WHERE concept = ? AND kind IN (?,?,?) AND answer != ''"
            " AND quote != ? ORDER BY ts DESC LIMIT 1",
            (concept, *_WEAK, quote),
        ).fetchone()
        if not prev:
            return
        outcome = "worked" if kind in _SUCCESS else "failed"
        con.execute(
            "INSERT INTO explanations (ts, concept, outcome, tutor_answer, learner_quote) VALUES (?,?,?,?,?)",
            (time.time(), concept, outcome, prev[0], quote),
        )
    if outcome != "worked":
        return
    path = _rules_path()
    today = time.strftime("%Y-%m-%d")
    existing = path.read_text(encoding="utf-8") if path.exists() else ""
    if f"{today} ({concept})" in existing:
        return  # one new rule per concept per day
    from deeptutor.services.llm import complete

    rule = await asyncio.wait_for(
        complete(
            prompt=(
                f"Earlier the learner was stuck on {concept}: \"{prev[1]}\"\n\nThe tutor explained:\n{prev[0][:1500]}\n\n"
                f"Later, on their own, the learner showed: \"{quote}\"\n\nExisting rules:\n{existing[-1500:]}"
            ),
            system_prompt=(
                "Write ONE short rule (max 25 words) about HOW to explain things to this learner, based on what "
                "made that explanation work. General teaching style, not the topic itself. If an existing rule "
                "already says it, reply exactly: SKIP"
            ),
            model=_EXTRACT_MODEL, temperature=0, max_tokens=80, reasoning_effort="none",
        ),
        timeout=60,
    )
    await _draft_understood(concept, prev[1], prev[0], quote)
    rule = " ".join((rule or "").split()).strip("-• ")
    if rule and rule.upper() != "SKIP":
        with path.open("a", encoding="utf-8") as f:
            f.write(f"- {today} ({concept}): {rule}\n")
        logger.info("chat mastery: new teaching rule from %s", concept)


# The learner's own view of a concept, written when they master it (see docs/second-brain-proposal.md).
# Course pages (the AI view) are read from PI_TUTOR_COURSE_CONCEPTS (<dir>/<course>/<concept>.md).
# Drafts are written inside pi-tutor's own data; a filer outside pi-tutor moves them into the second brain.
def _course_pages() -> dict[str, str]:
    root = os.environ.get("PI_TUTOR_COURSE_CONCEPTS", "")
    if not root or not Path(root).expanduser().is_dir():
        return {}
    base = Path(root).expanduser()
    return {p.relative_to(base).with_suffix("").as_posix(): p.read_text(encoding="utf-8", errors="replace")[:300]
            for p in sorted(base.glob("*/*.md"))}


def _drafts_dir() -> Path:
    from deeptutor.services.path_service import get_path_service

    return get_path_service().user_data_dir / "understood-drafts"


async def _draft_understood(concept: str, stuck_quote: str, tutor_answer: str, success_quote: str) -> None:
    from deeptutor.services.llm import complete

    pages = _course_pages()
    listing = "\n".join(pages) or "(no course pages)"
    raw = await asyncio.wait_for(
        complete(
            prompt=(
                f"Concept the learner just mastered: {concept}\n"
                f"Where they were stuck: \"{stuck_quote}\"\n"
                f"The explanation that worked:\n{tutor_answer[:2500]}\n"
                f"What they later showed on their own: \"{success_quote}\"\n\n"
                f"Course concept pages (course/page):\n{listing}"
            ),
            system_prompt=(
                "Write the learner's own revision page for this concept. Reply with JSON only: "
                '{"page": "<course/page from the list that this concept belongs to, or empty>", '
                '"slug": "<lowercase-hyphen file name for the concept>", "title": "<concept title>", '
                '"in_my_words": "<1-2 sentences, plain, in the learner\'s voice>", '
                '"example": "<the small worked example that clicked, with numbers>", '
                '"watch_out": "<one common mistake to avoid, or empty>"}'
            ),
            model=_EXTRACT_MODEL, temperature=0, max_tokens=500, reasoning_effort="none",
        ),
        timeout=60,
    )
    match = re.search(r"\{.*\}", raw or "", re.S)
    data = json.loads(match.group(0)) if match else {}
    slug = re.sub(r"[^a-z0-9-]+", "-", str(data.get("slug") or concept).lower()).strip("-")[:80]
    page = str(data.get("page") or "")
    course = page.split("/")[0] if page in pages else "general"
    link = f"[[university/concepts/{page}]]" if page in pages else ""
    today = time.strftime("%Y-%m-%d")
    body = "\n".join([
        "---",
        f"title: {data.get('title') or concept} — how I understood it",
        "type: understood",
        f"course: {course}",
        f"concept: {link}",
        f"mastered: {today}",
        "---",
        f"**In my words:** {data.get('in_my_words', '')}",
        "",
        f"**The example that clicked:** {data.get('example', '')}",
        "",
        f"**Where I got stuck before:** \"{stuck_quote}\"",
        "",
        f"**What I showed later:** \"{success_quote}\"",
        *([""] + [f"**Watch out:** {data['watch_out']}"] if data.get("watch_out") else []),
        "",
    ])
    out = _drafts_dir() / course / f"{slug}.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(body, encoding="utf-8")
    logger.info("chat mastery: understood draft %s", out)


def _learned_rules() -> str:
    path = _rules_path()
    lines = [l for l in path.read_text(encoding="utf-8").splitlines() if l.strip()] if path.exists() else []
    if not lines:
        return ""
    return "\n\nRules learned from what worked for this learner (newest first):\n" + "\n".join(reversed(lines[-15:]))


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


def record_code(language: str, original: str, code: str, stdout: str, stderr: str, exit_code: int) -> None:
    """The learner edited a code block and ran it: their edit plus the result is evidence too."""
    message = (
        f"(The learner edited the tutor's {language} code and ran it. A typo or unfamiliar syntax is NOT a "
        "concept gap; only judge what the edit shows they understand.)\n"
        f"Tutor's code:\n{original[:1500]}\n\nLearner's edited code:\n{code[:2000]}"
    )
    result = f"Exit code {exit_code}.\nOutput:\n{stdout[-800:]}\nErrors:\n{stderr[-800:]}"
    record_turn("run-code", f"run-{int(time.time() * 1000)}", message, result)


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


_STYLE_NEEDS = 8  # signals before a pattern is trusted
_STYLE_TUTOR = {
    "asked_for_answer": "They usually ask for the answer before trying. Ask for their first guess or attempt (one line is fine) before you explain, then build on it.",
    "tried_first": "They usually try first. Skip the warm-up and give feedback on their attempt straight away.",
    "accepted": "They often take answers without testing them. End with one small 'what would break if...' check they can answer in a line.",
    "pushed_back": "They question and correct answers. Give your reasoning and its limits so they can check it, and welcome disagreement.",
    "own_idea": "They bring their own ideas. Build on their idea first, then add yours as a second option.",
}


def style_profile() -> dict:
    """How the learner uses the AI: recency-weighted share per style, plus the dominant one once there is enough evidence."""
    with _db() as con:
        rows = con.execute("SELECT id, ts, style, quote FROM usage_style ORDER BY ts DESC").fetchall()
    now, acc = time.time(), {}
    for _, ts, style, _q in rows:
        acc[style] = acc.get(style, 0.0) + 0.5 ** ((now - ts) / 86400 / _HALF_LIFE_DAYS)
    total = sum(acc.values())
    shares = {k: round(v / total, 2) for k, v in sorted(acc.items(), key=lambda kv: -kv[1])} if total else {}
    top = next(iter(shares), None)
    return {
        "signals": len(rows), "shares": shares,
        "dominant": top if len(rows) >= _STYLE_NEEDS and shares.get(top, 0) >= 0.4 else None,
        "records": [{"id": i, "ts": ts, "style": st, "quote": q} for i, ts, st, q in rows[:30]],
    }


def _style_line() -> str:
    try:
        top = style_profile()["dominant"]
    except Exception:
        return ""
    return f"\nHow this learner uses AI: {_STYLE_TUTOR[top]}" if top else ""


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
10. Never mention mastery scores or turn the chat into a quiz.
11. When a process, flow, structure or relationship is easier to see than read, include one small diagram as a ```mermaid code block (flowchart, sequence or ER diagram, under 12 nodes), then explain it.
12. Take examples from the learner's own projects and daily AI use before inventing generic ones: search the llm-wiki tool (their projects and logs) or learning-memory, and name the project the example comes from. Search first; never ask the learner which project to use.
13. When the idea is best understood by changing something and watching the result (a slider for a parameter, stepping through an algorithm, a request moving through a system), add one small self-contained ```html block (inline CSS/JS, no external links, under 150 lines, a <title>) the learner can open in the side panel. Not for plain facts or definitions; end by asking what they noticed."""

# Method by mastery band, rewritten in our own words from worked-example fading, Socratic questioning and retrieval practice.
_METHODS = (
    (0.45, "weak", "worked example then fading: show one fully worked example step by step, then end with a near-identical second example where every step is given except the last, phrased as an open question"),
    (0.70, "middle", "Socratic: give the key idea briefly, then ask ONE question that makes them take the next step themselves"),
    (1.01, "strong", "retrieval challenge: answer briefly, then pose one slightly harder twist for them to try from memory"),
)


def _method(score: float) -> tuple[str, str]:
    return next((band, how) for limit, band, how in _METHODS if score < limit)


_FLOOR, _NEAR_TOP = 0.5, 0.06  # a concept matches if it scores >= 0.5 and within 0.06 of the best one
_PENDING: dict[str, asyncio.Task] = {}


def _cos(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    return dot / ((sum(x * x for x in a) ** 0.5) * (sum(y * y for y in b) ** 0.5) or 1)


async def _related(user_message: str) -> set[str]:
    """Concepts whose meaning is close to the message. Each concept is embedded as its name plus the
    learner's own quotes, cached until those quotes change."""
    from deeptutor.services.embedding.client import get_embedding_client

    with _db() as con:
        con.execute("CREATE TABLE IF NOT EXISTS concept_vec (concept TEXT PRIMARY KEY, text TEXT, vec TEXT)")
        if "text" not in [r[1] for r in con.execute("PRAGMA table_info(concept_vec)")]:
            con.execute("DROP TABLE concept_vec")
            con.execute("CREATE TABLE concept_vec (concept TEXT PRIMARY KEY, text TEXT, vec TEXT)")
        cached = {c: (t, json.loads(v)) for c, t, v in con.execute("SELECT concept, text, vec FROM concept_vec")}
        quotes: dict[str, list[str]] = {}
        for c, q in con.execute("SELECT concept, quote FROM evidence ORDER BY ts DESC"):
            quotes.setdefault(c, []).append(q[:150])
    texts = {c: f"{c}. " + " | ".join(qs[:3]) for c, qs in quotes.items()}
    stale = [c for c, t in texts.items() if cached.get(c, ("",))[0] != t]
    vecs = await get_embedding_client().embed([user_message[:2000], *[texts[c] for c in stale]])
    if stale:
        with _db() as con:
            con.executemany(
                "INSERT OR REPLACE INTO concept_vec VALUES (?, ?, ?)",
                [(c, texts[c], json.dumps(v)) for c, v in zip(stale, vecs[1:])],
            )
        cached.update({c: (texts[c], v) for c, v in zip(stale, vecs[1:])})
    scores = {c: _cos(vecs[0], cached[c][1]) for c in texts}
    best = max(scores.values(), default=0)
    return {c for c, sc in scores.items() if sc >= _FLOOR and sc >= best - _NEAR_TOP}


def start_match(user_message: str) -> None:
    """Begin the meaning lookup as soon as the message arrives, so it overlaps the rest of turn setup."""
    if user_message.strip() and user_message not in _PENDING:
        _PENDING[user_message] = asyncio.create_task(_related(user_message))


def _understood(concept: str) -> str:
    """Short excerpt of the learner's own "how I understood it" page for a concept, if they have one."""
    d = Path(os.environ.get("PI_TUTOR_UNDERSTOOD_DIR", "")).expanduser()
    if not os.environ.get("PI_TUTOR_UNDERSTOOD_DIR") or not d.is_dir():
        return ""
    words = set(re.findall(r"[a-z0-9]+", concept.lower()))
    for p in d.rglob("*.md"):
        text = p.read_text(encoding="utf-8", errors="replace")
        title = (re.search(r"^title:\s*(.+)$", text, re.M) or [None, p.stem])[1].lower()
        if words and words <= set(re.findall(r"[a-z0-9]+", title + " " + p.stem.replace("-", " "))):
            body = text.split("---", 2)[-1].strip()
            return " ".join(body.split())[:400]
    return ""


def _not_started(limit: int = 2) -> list[str]:
    """Course concept pages that share no word with anything tracked yet (the gap list's not-started ones)."""
    seen = set()
    for m in mastery():
        seen |= {w for w in re.findall(r"[a-z0-9]+", m["concept"].lower()) if len(w) > 3}
    out = []
    for page, text in _course_pages().items():
        title = (re.search(r"^title:\s*(.+)$", text, re.M) or [None, page.split("/")[-1].replace("-", " ")])[1]
        if not seen & {w for w in re.findall(r"[a-z0-9]+", title.lower()) if len(w) > 3}:
            out.append(title.strip())
    return out[:limit]


def _project_map(user_message: str) -> str:
    """The project map (made by the `project-map` command with Graphify) for a project named in the message."""
    from deeptutor.services.path_service import get_path_service

    d = get_path_service().user_data_dir / "project-maps"
    low = re.sub(r"[^a-z0-9]", "", user_message.lower())
    for p in sorted(d.glob("*.md")) if d.is_dir() else []:
        if re.sub(r"[^a-z0-9]", "", p.stem.lower()) in low:
            return (
                "\n\nProject map of the learner's own project (from its code; INFERRED links are guesses):\n"
                + p.read_text(encoding="utf-8")[:6000]
                + "\nTour mode: take ONE area at a time, most connected first. Read its real files with read_project_file, quote the key lines, show one Mermaid "
                "diagram of how it connects to the next area, then ask the learner to explain in their own words what "
                "the area does and why it exists before moving on. Never claim they understand it; their explanation decides."
            )
    listing = []
    for p in sorted(d.glob("*.md")) if d.is_dir() else []:
        src = re.search(r"^Source: (\S+)", p.read_text(encoding="utf-8"), re.M)
        listing.append(f"{p.stem} ({src.group(1) if src else '?'})")
    return (
        "\n\nThe learner's mapped projects: " + ", ".join(listing) + ". If they refer to one (even by description), "
        "call map_project with its name to load the map and tour it; to understand any other codebase (a folder or a git URL), "
        "call map_project with that source. Use read_project_file to quote the real code; never ask the learner to paste it."
    ) if listing else ""


# Code examples for any concept, from the learner's own mapped projects (topic-agnostic): once per map, a cheap call
# names what each code area teaches; areas and tracked concepts are then matched by meaning.
_AREA_JOBS: dict[str, asyncio.Task] = {}


async def _tag_areas(path: Path) -> None:
    from deeptutor.services.embedding.client import get_embedding_client
    from deeptutor.services.llm import complete

    text = path.read_text(encoding="utf-8")
    raw = await complete(
        prompt=text[:7000],
        system_prompt=(
            "For each numbered main area of this code project map, name 1-4 general concepts (any subject: programming, "
            "databases, statistics, networking, design...) a student would learn by reading that code. Reply with JSON only: "
            '{"areas": [{"area": "<the bold area title>", "concepts": ["..."]}]}'
        ),
        model=_EXTRACT_MODEL, temperature=0, max_tokens=900, reasoning_effort="none",
    )
    match = re.search(r"\{.*\}", raw or "", re.S)
    areas = (json.loads(match.group(0)) if match else {}).get("areas", [])
    areas = [a for a in areas if a.get("area") and a.get("concepts")]
    vecs = await get_embedding_client().embed([f"{', '.join(a['concepts'])}" for a in areas]) if areas else []
    for a, v in zip(areas, vecs):
        a["vec"] = v
    with _db() as con:
        con.execute("CREATE TABLE IF NOT EXISTS area_concepts (map TEXT PRIMARY KEY, mtime REAL, data TEXT)")
        con.execute("INSERT OR REPLACE INTO area_concepts VALUES (?,?,?)", (path.stem, path.stat().st_mtime, json.dumps(areas)))


def _code_examples(concepts: list[str]) -> dict[str, str]:
    """concept -> "project: area (what it teaches)" for the best-matching area of any mapped project."""
    from deeptutor.services.path_service import get_path_service

    d = get_path_service().user_data_dir / "project-maps"
    if not d.is_dir():
        return {}
    with _db() as con:
        con.execute("CREATE TABLE IF NOT EXISTS area_concepts (map TEXT PRIMARY KEY, mtime REAL, data TEXT)")
        tagged = {m: (t, json.loads(x)) for m, t, x in con.execute("SELECT map, mtime, data FROM area_concepts")}
        cvec = {c: json.loads(v) for c, v in con.execute("SELECT concept, vec FROM concept_vec")} if con.execute(
            "SELECT name FROM sqlite_master WHERE name='concept_vec'").fetchone() else {}
    for p in d.glob("*.md"):
        if tagged.get(p.stem, (0,))[0] != p.stat().st_mtime and (p.stem not in _AREA_JOBS or _AREA_JOBS[p.stem].done()):
            _AREA_JOBS[p.stem] = asyncio.create_task(_tag_areas(p))  # ready from the next message on
    out = {}
    for c in concepts:
        if c not in cvec:
            continue
        best = max(((_cos(cvec[c], a["vec"]), m, a) for m, (_, areas) in tagged.items() for a in areas if a.get("vec")),
                   default=(0, "", {}), key=lambda x: x[0])
        if best[0] >= 0.4:
            out[c] = f"{best[1]}: {best[2]['area']} (teaches {', '.join(best[2]['concepts'])})" + _snippet(d / f"{best[1]}.md", best[2]["area"], c)
    return out


def _snippet(map_path: Path, area_file: str, concept: str) -> str:
    """~25 real lines from the area's file, around the lines that share the most words with the concept."""
    src = re.search(r"^Source: (\S+)", map_path.read_text(encoding="utf-8"), re.M)
    if not src:
        return ""
    root = Path(src.group(1))
    path = next((p for p in (root / area_file, root / "src" / area_file) if p.is_file()), None)
    if path is None:
        hits = list(root.rglob(Path(area_file).name))
        path = hits[0] if hits else None
    if path is None:
        return ""
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    words = {w for w in re.findall(r"[a-z0-9]+", concept.lower()) if len(w) > 2}
    words |= {w.rstrip("s") for w in words}
    score = [sum(w in ln.lower() for w in words) for ln in lines]
    best = max(range(len(lines)), key=lambda i: sum(score[i:i + 25]), default=0)
    body = "\n".join(f"{i + 1}: {ln}" for i, ln in enumerate(lines[best:best + 25], best))
    rel = path.relative_to(root)
    return (f"\n  REAL CODE from {rel} (lines {best + 1}-{best + len(lines[best:best + 25])}). Quote and explain these real lines "
            f"FIRST, before any made-up example; say which file they come from:\n```\n{body}\n```")


async def prompt_block(user_message: str, limit: int = 4, mode: str | None = None) -> str:
    """The learner's teaching rules, plus a method for each tracked concept relevant to the message."""
    from deeptutor.services.discussion_modes import mode_block

    how = mode_block(mode) or _style_line()  # a chosen mode overrides the learned style; Auto uses the style. Goes last so it has the final word.
    words = {w for w in re.findall(r"[a-z0-9]+", user_message.lower()) if len(w) > 2}
    hits = [m for m in mastery() if words & set(re.findall(r"[a-z0-9]+", m["concept"]))]
    try:  # also match by meaning ("beta-binomial updating" -> "conjugate priors")
        task = _PENDING.pop(user_message, None) or asyncio.create_task(_related(user_message))
        t0 = time.monotonic()
        near = await asyncio.wait_for(task, timeout=3)
        logger.info("chat mastery: meaning match waited %.0f ms, matched %s", (time.monotonic() - t0) * 1000, sorted(near))
        hits += [m for m in mastery() if m["concept"] in near and m not in hits]
    except Exception:
        logger.debug("chat mastery: meaning match skipped", exc_info=True)
    hits = hits[:limit]
    practice = bool(re.search(r"\b(practi[cs]e|quiz|exercises?|questions?|test me|drill)\b", user_message, re.I))
    if practice and not hits:  # "give me practice" with no topic: practise the weakest concepts
        hits = mastery()[:3]
    if not hits:
        return _RULES + _learned_rules() + _project_map(user_message) + how
    lines = []
    try:
        examples = _code_examples([m["concept"] for m in hits])
    except Exception:
        logger.debug("chat mastery: code examples skipped", exc_info=True)
        examples = {}
    for m in hits:
        band, how = _method(m["mastery"])
        lines.append(f"- {m['concept']} ({band}, from {m['evidence']} past chat signals): {how}")
        if mine := _understood(m["concept"]):
            lines.append(f"  Their own page on it (reuse its words and example): {mine}")
        if ex := examples.get(m["concept"]):
            lines.append(f"  Example from their own code: {ex}. Read it with read_project_file and teach the concept through it.")
    extra = (
        "\nThey asked for practice: make the questions about these concepts, easiest for weak ones, "
        "a harder twist for strong ones."
        + (f" Add one starter question on a topic they have not touched yet: {'; '.join(fresh)}." if (fresh := _not_started()) else "")
        if practice else ""
    )
    return _RULES + _learned_rules() + _project_map(user_message) + "\n\nLearned from earlier chats, for the concepts in this message use:\n" + "\n".join(lines) + extra + how
