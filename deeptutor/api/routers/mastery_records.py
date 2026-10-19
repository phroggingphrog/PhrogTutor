"""What pi-tutor learned about the learner from chats and code runs: see it, and delete wrong records."""

from __future__ import annotations

from fastapi import APIRouter

from deeptutor.services import chat_mastery

router = APIRouter()


@router.get("")
def list_records():
    with chat_mastery._db() as con:
        rows = con.execute("SELECT id, ts, concept, kind, quote FROM evidence ORDER BY ts DESC").fetchall()
    evidence: dict[str, list[dict]] = {}
    for id_, ts, concept, kind, quote in rows:
        evidence.setdefault(concept, []).append({"id": id_, "ts": ts, "kind": kind, "quote": quote})
    return {"concepts": [{**m, "records": evidence.get(m["concept"], [])} for m in chat_mastery.mastery()]}


@router.delete("/{record_id}")
def delete_record(record_id: int):
    with chat_mastery._db() as con:
        con.execute("DELETE FROM evidence WHERE id = ?", (record_id,))
    return {"ok": True}


# Gap list: every course concept page (the AI view) with how far the learner is (docs/second-brain-proposal.md).
# A page is "mastered" when the learner has their own understood page linking to it (PI_TUTOR_UNDERSTOOD_DIR);
# otherwise its status comes from the closest tracked concept by meaning (same embeddings as the tutor).
@router.get("/gaps")
async def gaps():
    import json
    import os
    import re
    from pathlib import Path

    from deeptutor.services.embedding.client import get_embedding_client

    pages = chat_mastery._course_pages()
    understood_dir = Path(os.environ.get("PI_TUTOR_UNDERSTOOD_DIR", "")).expanduser()
    mastered = set()
    if os.environ.get("PI_TUTOR_UNDERSTOOD_DIR") and understood_dir.is_dir():
        for p in understood_dir.rglob("*.md"):
            m = re.search(r"^concept: \[\[university/concepts/([^\]]+)\]\]", p.read_text(encoding="utf-8"), re.M)
            if m:
                mastered.add(m.group(1))
    scores = {m["concept"]: m["mastery"] for m in chat_mastery.mastery()}
    titles = {k: (re.search(r"^title:\s*(.+)$", v, re.M) or [None, k.split("/")[-1].replace("-", " ")])[1] for k, v in pages.items()}
    near: dict[str, tuple[str, float]] = {}
    if scores and titles:
        with chat_mastery._db() as con:
            con.execute("CREATE TABLE IF NOT EXISTS page_vec (page TEXT PRIMARY KEY, title TEXT, vec TEXT)")
            cached = {p: (t, json.loads(v)) for p, t, v in con.execute("SELECT page, title, vec FROM page_vec")}
            cvec = {c: json.loads(v) for c, v in con.execute("SELECT concept, vec FROM concept_vec")} if con.execute(
                "SELECT name FROM sqlite_master WHERE name='concept_vec'").fetchone() else {}
        stale = [p for p, t in titles.items() if cached.get(p, ("",))[0] != t]
        missing = [c for c in scores if c not in cvec]
        client = get_embedding_client()
        if stale or missing:
            vecs = await client.embed([titles[p] for p in stale] + missing)
            with chat_mastery._db() as con:
                con.executemany("INSERT OR REPLACE INTO page_vec VALUES (?,?,?)",
                                [(p, titles[p], json.dumps(v)) for p, v in zip(stale, vecs)])
            cached.update({p: (titles[p], v) for p, v in zip(stale, vecs)})
            cvec.update(dict(zip(missing, vecs[len(stale):])))
        for page, (_, pv) in cached.items():
            best = max(((chat_mastery._cos(pv, cvec[c]), c) for c in scores if c in cvec), default=(0, ""))
            if best[0] >= 0.6:
                near[page] = (best[1], scores[best[1]])
    courses: dict[str, list[dict]] = {}
    for page in pages:
        course = page.split("/")[0]
        if page in mastered:
            status, via = "mastered", ""
        elif page in near:
            score = near[page][1]
            status, via = ("strong" if score >= 0.7 else "middle" if score >= 0.45 else "weak"), near[page][0]
        else:
            status, via = "not_started", ""
        courses.setdefault(course, []).append({"page": page, "title": titles[page], "status": status, "via": via})
    return {"courses": courses}
