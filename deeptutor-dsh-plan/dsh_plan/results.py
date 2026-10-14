"""Observe a handoff, forward owner corrections, link results."""
from __future__ import annotations

import uuid

from . import dsh_bridge_client as bridge
from .plan_records import Store


def link(store: Store, approval_id):
    h = store.handoff(approval_id)
    return bridge.DEEP_LINK.format(sid=h["session_id"]) if h else None


def observe(store: Store, approval_id, call=bridge._call):
    h = store.handoff(approval_id)
    r = call("observe", {"approval_id": approval_id, "session_id": h["session_id"], "after_seq": h["last_seq"]})
    with store.db:  # reconnect reuses the same session id and durable seq; never creates
        store.db.execute("UPDATE handoffs SET state=?, last_seq=?, result=? WHERE approval_id=?",
                         (r.get("state", h["state"]), r.get("seq", h["last_seq"]), r.get("result", h["result"]), approval_id))
    return store.handoff(approval_id) | {"link": link(store, approval_id)}


def correct(store: Store, approval_id, owner_message, request_id=None, call=bridge._call):
    h = store.handoff(approval_id)
    return call("correct", {"approval_id": approval_id, "session_id": h["session_id"],
                            "request_id": request_id or "corr-" + uuid.uuid4().hex, "message": owner_message})
