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
