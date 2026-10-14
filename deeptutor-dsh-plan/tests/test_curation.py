import pytest
from dsh_plan.curation import drive, records, retrieve


@pytest.fixture
def side(tmp_path):
    srcs = {
        "/r/agent-audit-20261012.md": "# Why work stalls\nPaperclip dispatch was used.",
        "/w/wiki/inbox/2026-10-13-idea.md": "# Tracker idea\nSave as an idea.",
        "/w/me/secret.md": "# Private\nx",
        "/r/deeptutor-dsh-baseline.md": "# Baseline\nPersistent DSH execution is required.",
    }
    p = str(tmp_path / "r.jsonl")
    records.write([records.classify(k, v) for k, v in srcs.items()], p)
    return p


def test_curation(side):
    r = retrieve.lookup("agent-audit-20261012.md", side)  # filename lookup that lexical search missed
    assert r["status"] == "ok" and r["hits"][0]["type"] == "historical_audit"
    assert "paperclip" in r["hits"][0]["note"]  # labelled superseded, not current architecture
    t = retrieve.lookup("why work stalls", side)  # title fallback
    assert t["status"] == "ok"
    inbox = retrieve.lookup("tracker idea", side)["hits"][0]
    assert inbox["approval"] == "proposed" and "PROPOSAL" in inbox["note"]
    assert retrieve.lookup("persistent dsh", side)["status"] == "missing"  # body text is not a title: missing, not failure
    assert retrieve.lookup("private", side)["status"] == "denied"
    assert retrieve.lookup("x", side + ".nope")["status"] == "search_failed"


def test_drive(tmp_path):
    p = str(tmp_path / "d.jsonl")
    ver = {"v": "one"}
    fetch = lambda fid: {"modified": "2026-10-14T00:00:00Z", "text": ver["v"]}
    assert drive.read_doc("F1", fetch, p)["state"] == "new"
    assert drive.read_doc("F1", fetch, p)["state"] == "unchanged"
    ver["v"] = "two"
    assert drive.read_doc("F1", fetch, p)["state"] == "changed"
    def gone(fid): raise PermissionError
    r = drive.read_doc("F1", gone, p)
    assert r["state"] == "no_access" and r["text"] is None
    def missing(fid): raise FileNotFoundError
    assert drive.read_doc("F1", missing, p)["state"] == "deleted"
