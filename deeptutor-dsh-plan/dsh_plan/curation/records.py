"""Sidecar records (records.jsonl). References and labels only; originals are never modified."""
from __future__ import annotations

import hashlib
import json
import os
import re
import time

DEFAULT = os.environ.get("DSH_CURATION", "/app/data/user/curation/records.jsonl")

# Dated owner decisions that outrank earlier proposals (verbatim from the 2026-10-14 baseline).
SUPERSEDED = {
    "paperclip": {"by": "owner 2026-10-12: 'paperclip already completely removed and flushed out'",
                  "note": "dispatch via Paperclip is historical, not current architecture"},
}
RESTRICTED = ("/me/", "/clinical/", "/finance/", "/private/")


def classify(path: str, text: str) -> dict:
    p = path.lower()
    title = next((l.lstrip("# ").strip() for l in text.splitlines() if l.startswith("#")), os.path.basename(path))
    if "/wiki/inbox/" in p:
        itype, approval = "agent_proposal", "proposed"
    elif "agent-audit" in p or "ai-use-facts" in p:
        itype, approval = "historical_audit", "unknown"
    elif "owner-asks-ledger" in p:
        itype, approval = "owner_request", "unknown"
    elif "baseline" in p or "handover" in p or "report" in p:
        itype, approval = "implementation_evidence", "unknown"
    else:
        itype, approval = "reference", "unknown"
    if re.search(r"(^|/)(test|fixture)s?[-_/]", p):
        itype = "test_traffic"
    low = text.lower()
    sup = [k for k in SUPERSEDED if k in low]
    return {
        "source_uri": path, "title": title, "information_type": itype, "approval_state": approval,
        "source_hash": hashlib.sha256(text.encode()).hexdigest(),
        "supersession_state": "conflicting" if sup else "current", "superseded_topics": sup,
        "access_class": "restricted" if any(r in p for r in RESTRICTED) else "project-private",
        "captured_at": time.time(),
    }


def write(records, path=DEFAULT):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        for r in records:
            f.write(json.dumps(r) + "\n")


def read(path=DEFAULT):
    try:
        return [json.loads(l) for l in open(path)]
    except FileNotFoundError:
        return None  # None = sidecar missing (a failure), [] = no records
