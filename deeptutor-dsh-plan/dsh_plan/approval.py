"""Owner approval. Only the owner-click route calls approve(); no model tool does."""
from __future__ import annotations

import time
import uuid

from .plan_records import Store


class ApprovalError(Exception):
    pass


def approve(store: Store, plan_id, version, sha256, approver, permission):
    """Bind approval to the exact version+hash. `approver` must be an authenticated owner id."""
    if not approver or approver in ("assistant", "model", "agent"):
        raise ApprovalError("approval needs an authenticated owner")
    plan = store.plan(plan_id, version)
    if not plan:
        raise ApprovalError("unknown plan/version")
    if plan["sha256"] != sha256:
        raise ApprovalError("hash mismatch: plan text changed")
    if plan["state"] != "proposed":
        raise ApprovalError(f"plan is {plan['state']}, not proposed")
    if plan["version"] != store.plan(plan_id)["version"]:
        raise ApprovalError("not the latest version")
    aid = "appr-" + uuid.uuid4().hex[:12]
    with store.db:
        store.db.execute("UPDATE plans SET state='approved' WHERE plan_id=? AND version=?", (plan_id, version))
        store.db.execute("INSERT INTO approvals VALUES(?,?,?,?,?,?,?,?)",
                         (aid, plan_id, version, sha256, approver, permission, time.time(), "active"))
    return store.approval(aid)


def reject(store: Store, plan_id, version, approver):
    if not approver or approver in ("assistant", "model", "agent"):
        raise ApprovalError("rejection needs an authenticated owner")
    with store.db:
        store.db.execute("UPDATE plans SET state='rejected' WHERE plan_id=? AND version=? AND state='proposed'", (plan_id, version))


def prepare_handoff(store: Store, approval_id):
    """Store session/request ids BEFORE any create/prompt; idempotent, so retries reuse them."""
    appr = store.approval(approval_id)
    if not appr or appr["state"] != "active":
        raise ApprovalError("approval missing or invalidated")
    plan = store.plan(appr["plan_id"], appr["version"])
    if plan["state"] != "approved" or plan["sha256"] != appr["sha256"]:
        raise ApprovalError("plan no longer matches approval")
    with store.db:
        store.db.execute("INSERT OR IGNORE INTO handoffs VALUES(?,?,?,?,?,?,?,?)",
                         (approval_id, "session-" + uuid.uuid4().hex, "req-" + uuid.uuid4().hex, "",
                          "pending", 0, "", time.time()))
    return store.handoff(approval_id)
