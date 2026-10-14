"""Exact-path / title fallback over the sidecar. Missing record != search failure."""
from __future__ import annotations

from . import records


def lookup(query: str, path=records.DEFAULT, allow_restricted=False):
    recs = records.read(path)
    if recs is None:
        return {"status": "search_failed", "reason": "curation sidecar not readable", "hits": []}
    q = query.lower().strip()
    hits = [r for r in recs if q == r["source_uri"].lower() or q == r["source_uri"].lower().rsplit("/", 1)[-1]]
    if not hits:
        hits = [r for r in recs if q in r["title"].lower()]
    if not allow_restricted:
        denied = [r for r in hits if r["access_class"] == "restricted"]
        hits = [r for r in hits if r["access_class"] != "restricted"]
        if denied and not hits:
            return {"status": "denied", "reason": "restricted source", "hits": []}
    if not hits:
        return {"status": "missing", "reason": "no curated record matches", "hits": []}
    return {"status": "ok", "hits": [_view(r) for r in hits]}


def current_decisions(topic: str, path=records.DEFAULT):
    """Owner decisions only; proposals and superseded topics are labelled, never promoted."""
    recs = records.read(path) or []
    out = []
    for r in recs:
        if topic.lower() in r["title"].lower() or topic.lower() in r["source_uri"].lower():
            out.append(_view(r))
    return out


def _view(r):
    note = ""
    if r["approval_state"] == "proposed":
        note = "PROPOSAL, not approved by the owner"
    if r["supersession_state"] == "conflicting":
        note = (note + "; " if note else "") + "mentions superseded topics: " + ", ".join(r["superseded_topics"])
    return {"source_uri": r["source_uri"], "title": r["title"], "type": r["information_type"],
            "approval": r["approval_state"], "captured_at": r["captured_at"], "note": note}
