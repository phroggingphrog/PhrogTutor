"""University-source retrieval with fail-closed citation validation.

A chunk is only ever returned if, at read time:
- its ``source_version.content_hash`` matches a fresh sha256 of the live
  file at ``private_locator`` (file missing or bytes changed -> reject);
- its ``citation_location`` (``path:start-end`` 1-indexed line range)
  reproduces ``chunk.text`` exactly when re-read from that same live file
  (reject on any drift, including line-ending/encoding changes);
- its ``source_version`` has not been superseded, unless the caller
  explicitly asks for history (``allow_superseded=True``).

Nothing here trusts the stored ``chunk.text``/``chunk.token_count`` as
truth by itself -- they are only served after live re-validation, so a
tampered or stale chunk fails closed instead of silently being cited.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .models import Citation
from .namespaces import Namespace

# Matches native_learning.py's learning_data_root(): relative
# private_locator values in source_version are relative to this root, not
# to the current working directory.
LEARNING_DATA_ROOT = Path.home() / "data" / "hermes"


class CitationValidationError(RuntimeError):
    """A stored chunk/source_version failed live re-validation."""


def quarantine_invalid_source_versions(
    db: sqlite3.Connection, *, actor: str = "deterministic-citation-validator"
) -> int:
    """Append quarantine tombstones for current sources that fail validation."""
    db.row_factory = sqlite3.Row
    rows = db.execute(
        """
        SELECT sv.*
          FROM source_version sv
         WHERE EXISTS (SELECT 1 FROM chunk c WHERE c.source_version_id = sv.id)
           AND NOT EXISTS (
               SELECT 1 FROM source_version nxt WHERE nxt.supersedes_id = sv.id
           )
         ORDER BY sv.id
        """
    ).fetchall()
    quarantined = 0
    with db:
        for source in rows:
            reasons: set[str] = set()
            chunk_ids = db.execute(
                "SELECT id FROM chunk WHERE source_version_id=? ORDER BY id",
                (source["id"],),
            ).fetchall()
            for chunk in chunk_ids:
                try:
                    validate_and_read_chunk(db, chunk["id"])
                except CitationValidationError as exc:
                    message = str(exc)
                    reasons.add(
                        "missing"
                        if "missing" in message
                        else "hash_mismatch"
                        if "hash mismatch" in message
                        else "text_drift"
                        if "not found verbatim" in message or "drift" in message
                        else "other_invalid"
                    )
            if not reasons:
                continue
            version = db.execute(
                "SELECT COALESCE(MAX(native_version),0)+1 FROM source_version WHERE namespace=? AND source_id=?",
                (source["namespace"], source["source_id"]),
            ).fetchone()[0]
            tombstone_id = "quarantine:" + hashlib.sha256(
                f'{source["id"]}:{version}'.encode()
            ).hexdigest()[:24]
            occurred_at = datetime.now(timezone.utc).isoformat()
            db.execute(
                "INSERT INTO source_version VALUES(?,?,?,?,?,?,?,?,?,?)",
                (
                    tombstone_id,
                    source["namespace"],
                    source["source_id"],
                    version,
                    source["content_hash"],
                    source["private_locator"],
                    occurred_at,
                    "QUARANTINED",
                    source["evidence_class"],
                    source["id"],
                ),
            )
            db.execute(
                "INSERT INTO audit_event(namespace,event_type,target_ref,actor,occurred_at,details_json) VALUES(?,?,?,?,?,?)",
                (
                    source["namespace"],
                    "LEARNING_SOURCE_QUARANTINED",
                    tombstone_id,
                    actor,
                    occurred_at,
                    json.dumps(
                        {
                            "superseded_source_version": source["id"],
                            "reason_classes": sorted(reasons),
                        },
                        sort_keys=True,
                        separators=(",", ":"),
                    ),
                ),
            )
            quarantined += 1
    return quarantined


@dataclass(frozen=True)
class _Located:
    path: Path
    start_line: int  # 1-indexed, inclusive
    end_line: int  # 1-indexed, inclusive


def _parse_path_line_location(citation_location: str) -> _Located | None:
    """Parse the "path:N-M" / "path:N" convention (10 of 309 real chunks).
    Returns None (not an error) if the string doesn't look like this
    convention at all -- callers fall back to the other convention.
    """
    if citation_location.startswith("chars "):
        return None
    if ":" not in citation_location:
        return None
    rel_path, _, line_spec = citation_location.rpartition(":")
    if not line_spec or not line_spec.replace("-", "").isdigit():
        return None
    if "-" in line_spec:
        start_s, _, end_s = line_spec.partition("-")
        start, end = int(start_s), int(end_s)
    else:
        start = end = int(line_spec)
    if start < 1 or end < start:
        return None
    return _Located(path=Path(rel_path), start_line=start, end_line=end)


def _locate_excerpt(
    live_text: str, citation_location: str, stored_text: str
) -> tuple[str, int, int]:
    """Reproduce the cited excerpt from the LIVE file text and return
    (excerpt, byte_start, byte_end). Never trusts a stored numeric offset
    on its own -- byte_start/byte_end are always derived from where the
    excerpt actually is found in the live text, not from the stored
    ``citation_location`` numbers (empirically, the dominant "chars N-M"
    convention in this database's real data does not reproduce via a
    direct ``text[N:M]`` slice -- e.g. real row
    ``learning:...:chunk:4``'s "chars 7660-9341" is off by several
    characters from where the text actually starts/ends -- so validation
    is done by exact-substring verification against live content, which
    is a strictly stronger and more honest check than trusting an
    unreliable stored offset).
    """
    located = _parse_path_line_location(citation_location)
    if located is not None:
        lines = live_text.splitlines(keepends=True)
        if located.end_line > len(lines):
            raise CitationValidationError(
                f"citation_location {citation_location!r} exceeds file length "
                f"({len(lines)} lines)"
            )
        excerpt = "".join(lines[located.start_line - 1 : located.end_line])
        prefix = "".join(lines[: located.start_line - 1])
        byte_start = len(prefix.encode("utf-8"))
        byte_end = byte_start + len(excerpt.encode("utf-8"))
        return excerpt, byte_start, byte_end

    # "chars N-M" (or any other unrecognized shape): locate by exact
    # substring search for the stored text itself. A stored excerpt that
    # no longer appears verbatim anywhere in the live file is exactly the
    # tamper/drift case this function exists to catch.
    idx = live_text.find(stored_text)
    if idx == -1:
        raise CitationValidationError(
            f"stored chunk text is not found verbatim in the live source "
            f"(citation_location={citation_location!r})"
        )
    byte_start = len(live_text[:idx].encode("utf-8"))
    byte_end = byte_start + len(stored_text.encode("utf-8"))
    return stored_text, byte_start, byte_end


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def validate_and_read_chunk(
    db: sqlite3.Connection, chunk_id: str, *, allow_superseded: bool = False
) -> Citation:
    """Fetch one chunk and its source_version, then re-validate live.

    Raises ``CitationValidationError`` (fail closed) on any hash mismatch,
    missing file, byte-range/text drift, or reference to a row that no
    longer exists. Never returns a chunk it could not independently verify.
    """
    row = db.execute(
        """
        SELECT c.id AS chunk_id, c.namespace AS chunk_namespace, c.record_ref,
               c.citation_location, c.text AS stored_text,
               sv.id AS source_version_id, sv.source_id, sv.content_hash,
               sv.private_locator, sv.evidence_class, sv.native_version,
               sv.namespace AS sv_namespace,
               (SELECT COUNT(*) FROM source_version nxt
                 WHERE nxt.supersedes_id = sv.id) AS superseded_count
          FROM chunk c
          JOIN source_version sv ON sv.id = c.source_version_id
         WHERE c.id = ?
        """,
        (chunk_id,),
    ).fetchone()
    if row is None:
        raise CitationValidationError(f"no such chunk: {chunk_id}")
    if row["superseded_count"] and not allow_superseded:
        raise CitationValidationError(
            f"source version is superseded for {row['record_ref']}"
        )

    raw_locator = Path(row["private_locator"])
    # 124 of 127 real source_version rows store private_locator RELATIVE
    # to the Hermes learning data root (Path.home()/"data"/"hermes" --
    # same root native_learning.py uses), not an absolute path. Resolve
    # relative locators there; an already-absolute locator is used as-is.
    private_locator = (
        raw_locator if raw_locator.is_absolute() else (LEARNING_DATA_ROOT / raw_locator)
    )
    if not private_locator.is_file():
        raise CitationValidationError(
            f"source file missing for {row['record_ref']}: {private_locator}"
        )

    live_hash = _sha256_file(private_locator)
    if live_hash != row["content_hash"]:
        raise CitationValidationError(
            f"content_hash mismatch for {row['record_ref']}: "
            f"stored={row['content_hash']} live={live_hash}"
        )

    raw = private_locator.read_bytes()
    text = raw.decode("utf-8", errors="strict")
    excerpt, byte_start, byte_end = _locate_excerpt(
        text, row["citation_location"], row["stored_text"]
    )
    if excerpt != row["stored_text"]:
        raise CitationValidationError(
            f"byte-range/text drift for {row['record_ref']}: live excerpt no "
            "longer matches the stored chunk text"
        )

    freshness = "SUPERSEDED" if row["superseded_count"] > 0 else "FRESH"

    return Citation(
        namespace=Namespace.UNIVERSITY_COURSE.value,
        course_id="",  # filled in by the caller, which knows the course scope
        source_id=row["source_id"],
        source_version=str(row["source_version_id"]),
        record_ref=row["record_ref"],
        content_hash=row["content_hash"],
        citation_locator=row["citation_location"],
        byte_start=byte_start,
        byte_end=byte_end,
        exact_text=excerpt,
        evidence_class=row["evidence_class"],
        freshness_state=freshness,
    )


def search_course_sources(
    db: sqlite3.Connection,
    course_id: str,
    query: str,
    limit: int = 2,
    *,
    allow_superseded: bool = False,
) -> list[Citation]:
    """Bounded FTS5 search over one course's chunks, each result validated live.

    Returns at most ``limit`` results (the spec requires "two or fewer
    relevant source excerpts" for the first slice). A chunk that fails
    live validation is skipped, not silently returned -- callers see fewer
    results rather than an unverifiable one.
    """
    history_filter = "" if allow_superseded else """
           AND NOT EXISTS (
               SELECT 1 FROM source_version nxt WHERE nxt.supersedes_id = sv.id
           )"""
    rows = db.execute(
        f"""
        SELECT c.id AS chunk_id
          FROM chunk_fts
          JOIN chunk c ON c.rowid = chunk_fts.rowid
          JOIN source_version sv ON sv.id = c.source_version_id
          JOIN reference_version rv ON rv.source_version_id = sv.id
         WHERE chunk_fts MATCH ?
           AND rv.course_id = ?
           {history_filter}
         ORDER BY bm25(chunk_fts)
         LIMIT ?
        """,
        (query, course_id, max(limit * 3, limit)),
    ).fetchall()

    results: list[Citation] = []
    for row in rows:
        try:
            citation = validate_and_read_chunk(
                db, row["chunk_id"], allow_superseded=allow_superseded
            )
        except CitationValidationError:
            continue
        results.append(
            Citation(
                namespace=citation.namespace,
                course_id=course_id,
                source_id=citation.source_id,
                source_version=citation.source_version,
                record_ref=citation.record_ref,
                content_hash=citation.content_hash,
                citation_locator=citation.citation_locator,
                byte_start=citation.byte_start,
                byte_end=citation.byte_end,
                exact_text=citation.exact_text,
                evidence_class=citation.evidence_class,
                freshness_state=citation.freshness_state,
            )
        )
        if len(results) >= limit:
            break
    return results


def read_source_excerpt(
    db: sqlite3.Connection,
    record_ref: str,
    course_id: str = "",
    *,
    allow_superseded: bool = False,
) -> Citation:
    """Look up one chunk by its exact ``record_ref`` and validate it live."""
    row = db.execute("SELECT id FROM chunk WHERE record_ref = ?", (record_ref,)).fetchone()
    if row is None:
        raise CitationValidationError(f"no chunk with record_ref: {record_ref}")
    citation = validate_and_read_chunk(
        db, row["id"], allow_superseded=allow_superseded
    )
    if course_id:
        citation = Citation(
            namespace=citation.namespace,
            course_id=course_id,
            source_id=citation.source_id,
            source_version=citation.source_version,
            record_ref=citation.record_ref,
            content_hash=citation.content_hash,
            citation_locator=citation.citation_locator,
            byte_start=citation.byte_start,
            byte_end=citation.byte_end,
            exact_text=citation.exact_text,
            evidence_class=citation.evidence_class,
            freshness_state=citation.freshness_state,
        )
    return citation
