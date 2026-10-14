import pytest
from dsh_plan import approval, handoff, results
from dsh_plan.dsh_bridge_client import BridgeBlocked
from dsh_plan.plan_records import Store


@pytest.fixture
def s(tmp_path):
    return Store(str(tmp_path / "t.sqlite"))


def test_flow(s):
    p1 = s.draft("deeptutor", "plan v1", "ws1")
    # reject -> no handoff possible
    approval.reject(s, p1["plan_id"], 1, "owner")
    with pytest.raises(approval.ApprovalError):
        approval.approve(s, p1["plan_id"], 1, p1["sha256"], "owner", "ro")
    # model cannot approve
    p = s.draft("deeptutor", "plan A", "ws1")
    with pytest.raises(approval.ApprovalError):
        approval.approve(s, p["plan_id"], 1, p["sha256"], "assistant", "ro")
    # wrong hash refused
    with pytest.raises(approval.ApprovalError):
        approval.approve(s, p["plan_id"], 1, "bad", "owner", "ro")
    # approve v1, revise -> v1 approval invalid for handoff
    a1 = approval.approve(s, p["plan_id"], 1, p["sha256"], "owner", "ro")
    p2 = s.draft("deeptutor", "plan B", "ws1", plan_id=p["plan_id"])
    assert p2["version"] == 2
    with pytest.raises(approval.ApprovalError):
        approval.prepare_handoff(s, a1["approval_id"])
    a2 = approval.approve(s, p["plan_id"], 2, p2["sha256"], "owner", "ro")
    calls = []

    def fake(op, body):
        calls.append((op, body["session_id"], body["request_id"]))
        return {"workspace_id": "w1"}

    h = handoff.submit(s, a2["approval_id"], call=fake)
    h2 = handoff.submit(s, a2["approval_id"], call=fake)  # duplicate
    assert len(calls) == 1 and h["session_id"] == h2["session_id"]
    assert results.link(s, a2["approval_id"]).endswith(h["session_id"])


def test_retry_same_ids_and_blocked(s):
    p = s.draft("deeptutor", "x", "ws1")
    a = approval.approve(s, p["plan_id"], 1, p["sha256"], "owner", "ro")
    seen = []

    def boom(op, body):
        seen.append((body["session_id"], body["request_id"]))
        raise RuntimeError("down")

    with pytest.raises(RuntimeError):
        handoff.submit(s, a["approval_id"], call=boom)
    with pytest.raises(RuntimeError):
        handoff.submit(s, a["approval_id"], call=boom)
    assert seen[0] == seen[1]  # same session + request on retry

    def blocked(op, body):
        raise BridgeBlocked("no bridge")

    with pytest.raises(BridgeBlocked):
        handoff.submit(s, a["approval_id"], call=blocked)
    assert s.handoff(a["approval_id"])["state"] == "blocked"
