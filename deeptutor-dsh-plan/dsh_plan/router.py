"""Owner-click API. Mount with: app.include_router(dsh_plan.router.router, prefix='/api/dsh-plan', dependencies=_auth)."""
from __future__ import annotations

import hmac
import os

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel

from . import approval, handoff, results
from .plan_records import Store

try:  # owner identity comes from the login token, never from model text
    from deeptutor.api.routers.auth import require_auth
except Exception:  # unit tests without DeepTutor installed
    def require_auth():
        raise HTTPException(401, 'owner login required')

router = APIRouter()


# Owner-facing permission modes -> DSH permission presets.
MODES = {"ask": "workspace-write", "auto": "auto", "bypass": "danger-full-access"}


class Approve(BaseModel):
    version: int
    sha256: str
    mode: str = "ask"
    confirm_bypass: bool = False  # bypass (no sandbox, no prompts) needs a second deliberate flag


def _owner(payload) -> str:
    if payload is None or getattr(payload, "role", "") != "admin" or not getattr(payload, "username", ""):
        raise HTTPException(403, "owner (admin) login required")
    return payload.username


@router.get("/{plan_id}")
def get_plan(plan_id: str, payload=Depends(require_auth)):
    """Plan row plus the latest approval/handoff (stored values; the card polls /handoff for live status)."""
    _owner(payload)
    store = Store()
    plan = store.plan(plan_id)
    if not plan:
        return {}
    row = store.db.execute(
        "SELECT a.approval_id, a.permission, h.state, h.result FROM approvals a "
        "LEFT JOIN handoffs h ON h.approval_id=a.approval_id WHERE a.plan_id=? AND a.version=? "
        "ORDER BY a.approved_at DESC LIMIT 1", (plan_id, plan["version"])).fetchone()
    if row:
        plan["approval_id"], plan["permission"] = row["approval_id"], row["permission"]
        plan["handoff_state"], plan["result"] = row["state"] or "", row["result"] or ""
        plan["link"] = results.link(store, row["approval_id"]) if row["state"] else None
    return plan


@router.get("/{plan_id}/handoff/{approval_id}")
def get_handoff(plan_id: str, approval_id: str, payload=Depends(require_auth)):
    _owner(payload)
    store = Store()
    if not store.handoff(approval_id):
        return {}
    try:
        return results.observe(store, approval_id)
    except Exception:  # bridge unreachable: show what is stored
        return store.handoff(approval_id) | {"link": results.link(store, approval_id)}


@router.post("/{plan_id}/approve")
def approve(plan_id: str, body: Approve, payload=Depends(require_auth)):
    store = Store()
    owner = _owner(payload)  # 403 before anything is touched
    if body.mode not in MODES:
        raise HTTPException(422, f"mode must be one of {sorted(MODES)}")
    if body.mode == "bypass" and not body.confirm_bypass:
        raise HTTPException(409, "bypass needs confirm_bypass=true")
    try:
        a = approval.approve(store, plan_id, body.version, body.sha256, owner, MODES[body.mode])
    except approval.ApprovalError as e:
        raise HTTPException(409, str(e))
    try:
        h = handoff.submit(store, a["approval_id"])
    except Exception as e:  # bridge blocked/failed: approval stays, no fallback
        return {"approval_id": a["approval_id"], "state": store.handoff(a["approval_id"])["state"], "error": str(e)}
    return {"approval_id": a["approval_id"], "state": h["state"], "link": results.link(store, a["approval_id"])}


@router.post("/{plan_id}/reject")
def reject(plan_id: str, version: int, payload=Depends(require_auth)):
    approval.reject(Store(), plan_id, version, _owner(payload))
    return {"ok": True}


class Callback(BaseModel):
    approval_id: str
    state: str
    seq: int = 0
    result: str = ""


@router.post("/callback")
async def callback(body: Callback, request: Request, authorization: str = Header(default="")):
    """DSH wake-up: the bridge calls this once when a handed-off session finishes."""
    token = os.environ.get("DSH_BRIDGE_TOKEN", "")
    got = authorization.removeprefix("Bearer ")
    if len(token) < 32 or not hmac.compare_digest(got.encode(), token.encode()):
        raise HTTPException(401, "bad token")
    if body.state not in ("completed", "failed"):
        raise HTTPException(422, "only completed/failed wake DeepTutor")
    store = Store()
    h = store.handoff(body.approval_id)
    if not h:
        raise HTTPException(404, "unknown approval")
    if h["state"] in ("completed", "failed"):
        return {"ok": True, "woke": False}  # duplicate callback: already handled
    with store.db:
        store.db.execute("UPDATE handoffs SET state=?, last_seq=?, result=? WHERE approval_id=?",
                         (body.state, body.seq, body.result, body.approval_id))
    appr = store.approval(body.approval_id)
    plan = store.plan(appr["plan_id"], appr["version"])
    sid = plan.get("source_session_id") or ""
    woke = False
    if sid and sid != "e2e-test":
        text = (f"[DSH finished] Plan {plan['plan_id']} v{plan['version']} {body.state}. "
                f"Session: {results.link(store, body.approval_id)}\n\nLast reply from DSH:\n{body.result}")
        try:
            from deeptutor.app.container import get_application_container

            container = getattr(request.app.state, "application_container", None) or get_application_container()
            await container.turns.start_turn({"content": text, "session_id": sid, "capability": "chat"})
            woke = True
        except Exception as e:  # result is stored either way; the panel still shows it
            return {"ok": True, "woke": False, "wake_error": str(e)[:200]}
    return {"ok": True, "woke": woke}
