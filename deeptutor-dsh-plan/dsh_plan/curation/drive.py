"""Drive provenance. Wraps the existing read-only `google_*` tools (my-accounts MCP); no new connector.

`fetch` is the connector call: fetch(file_id) -> {"modified": str, "text": str} or raises PermissionError/FileNotFoundError.
"""
from __future__ import annotations

import hashlib
import json
import os
import time

DEFAULT = os.environ.get("DSH_DRIVE_RECORDS", "/app/data/user/curation/drive.jsonl")


def _load(path):
    try:
        return {r["file_id"]: r for r in map(json.loads, open(path))}
    except FileNotFoundError:
        return {}


def _save(recs, path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        for r in recs.values():
            f.write(json.dumps(r) + "\n")


def read_doc(file_id, fetch, path=DEFAULT):
    """Read a doc, record id/hash/modified/captured. Never serve cached text when access is lost."""
    recs = _load(path)
    prev = recs.get(file_id)
    try:
        got = fetch(file_id)
    except (PermissionError, FileNotFoundError) as e:
        state = "no_access" if isinstance(e, PermissionError) else "deleted"
        if prev:
            prev.update(state=state, checked_at=time.time()); _save(recs, path)
        return {"state": state, "text": None}  # cached content deliberately not returned
    h = hashlib.sha256(got["text"].encode()).hexdigest()
    state = "new" if not prev else ("changed" if prev["content_hash"] != h else "unchanged")
    recs[file_id] = {"file_id": file_id, "modified": got["modified"], "content_hash": h,
                     "captured_at": time.time(), "state": state, "access_policy": "drive-read-only"}
    _save(recs, path)
    return {"state": state, "text": got["text"], "record": recs[file_id]}


def mcp_fetch(email, url=None):
    """Build `fetch` over the existing my-accounts MCP (`google_get_doc_content`). Read-only tool only."""
    import urllib.request

    url = url or os.environ.get("MY_ACCOUNTS_MCP", "http://192.168.122.1:8913/mcp")
    hdr = {"content-type": "application/json", "accept": "application/json,text/event-stream"}

    def post(body):
        r = urllib.request.urlopen(urllib.request.Request(url, json.dumps(body).encode(), hdr), timeout=60)
        raw = r.read().decode()
        data = next(l[5:] for l in raw.splitlines() if l.startswith("data:")) if "data:" in raw else raw
        return json.loads(data)

    def fetch(file_id):
        res = post({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                    "params": {"name": "google_get_doc_content",
                               "arguments": {"user_google_email": email, "document_id": file_id}}})["result"]
        text = "".join(c.get("text", "") for c in res.get("content", []))
        if res.get("isError"):
            low = text.lower()
            if "404" in low or "not found" in low:
                raise FileNotFoundError(file_id)
            if "403" in low or "permission" in low or "forbidden" in low:
                raise PermissionError(file_id)
            raise RuntimeError(text[:200])
        return {"modified": "", "text": text}  # modified time is filled by the listing tool when known

    return fetch
