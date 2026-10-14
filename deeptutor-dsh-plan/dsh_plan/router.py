"""Owner-click API. Mount with: app.include_router(dsh_plan.router.router, prefix='/api/dsh-plan', dependencies=_auth)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from . import approval, handoff, results
from .plan_records import Store

try:  # owner identity comes from the login token, never from model text
    from deeptutor.api.routers.auth import require_auth
except Exception:  # unit tests without DeepTutor installed
    def require_auth():
        raise HTTPException(401, 'owner login required')

router = APIRouter()


class Approve(BaseModel):
    version: int
    sha256: str
    permission: str = "workspace-write"


def _owner(payload) -> str:
    if payload is None or getattr(payload, "role", "") != "admin" or not getattr(payload, "username", ""):
        raise HTTPException(403, "owner (admin) login required")
    return payload.username


@router.get("/{plan_id}")
def get_plan(plan_id: str, payload=Depends(require_auth)):
    _owner(payload)
    return Store().plan(plan_id) or {}


@router.get("/{plan_id}/handoff/{approval_id}")
def get_handoff(plan_id: str, approval_id: str, payload=Depends(require_auth)):
    _owner(payload)
    store = Store()
    return results.observe(store, approval_id) if store.handoff(approval_id) else {}


@router.post("/{plan_id}/approve")
def approve(plan_id: str, body: Approve, payload=Depends(require_auth)):
    store = Store()
    owner = _owner(payload)  # 403 before anything is touched
    try:
        a = approval.approve(store, plan_id, body.version, body.sha256, owner, body.permission)
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
