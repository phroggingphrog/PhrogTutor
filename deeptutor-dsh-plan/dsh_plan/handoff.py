"""Owner-approved plan -> one persistent DSH session (same ids on every retry)."""
from __future__ import annotations

from . import dsh_bridge_client as bridge
from .approval import prepare_handoff
from .plan_records import Store

PROMPT = ("Approved plan (id {plan_id}, version {version}, sha256 {sha}, approval {aid}).\n"
          "Execute exactly this plan, then report the result files and checks.\n\n{content}")


def submit(store: Store, approval_id, call=bridge._call):
    h = prepare_handoff(store, approval_id)
    if h["state"] in ("admitted", "running", "completed"):
        return h  # duplicate submit: same session, no second prompt
    appr = store.approval(approval_id)
    plan = store.plan(appr["plan_id"], appr["version"])
    body = {"approval_id": approval_id, "plan_id": plan["plan_id"], "version": plan["version"],
            "sha256": plan["sha256"], "workspace_key": plan["workspace_key"],
            "permission": appr["permission"], "session_id": h["session_id"], "request_id": h["request_id"],
            "prompt": PROMPT.format(plan_id=plan["plan_id"], version=plan["version"], sha=plan["sha256"],
                                    aid=approval_id, content=plan["content"])}
    try:
        res = call("handoff", body)
    except bridge.BridgeBlocked:
        _set(store, approval_id, "blocked")
        raise
    except Exception:
        _set(store, approval_id, "failed")  # ids are kept; resubmit retries the same session/request
        raise
    with store.db:
        store.db.execute("UPDATE handoffs SET state='admitted', workspace_id=? WHERE approval_id=?",
                         (res.get("workspace_id", ""), approval_id))
    return store.handoff(approval_id)


def _set(store, approval_id, state):
    with store.db:
        store.db.execute("UPDATE handoffs SET state=? WHERE approval_id=?", (state, approval_id))
