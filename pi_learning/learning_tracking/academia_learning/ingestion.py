"""University-source ingestion: lecture transcripts (and similar new
material) into the UNIVERSITY_COURSE namespace, plus new objectives into
LEARNER_EVIDENCE -- both idempotent, both fail-closed-verified immediately
after insert.

This is the "apply approved changes and verify" step of the
lecture-transcript-notes skill (see
~/.hermes/profiles/academia/skills/productivity/lecture-transcript-notes/).
The extraction judgment (what counts as a taught concept, what quote
supports it) stays with the agent; this module only performs the
deterministic, safe part: hashing, chunking-with-citation, idempotent
insert, and read-back verification against citations.validate_and_read_chunk.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from dataclasses import dataclass
from pathlib import Path

from .citations import CitationValidationError, validate_and_read_chunk
from .db import now_iso
from .objectives import Objective, get_learning_objective


class IngestionError(RuntimeError):
    pass


def _snapshot_source(db: sqlite3.Connection, content: bytes) -> tuple[str, Path]:
    content_hash = hashlib.sha256(content).hexdigest()
    db_file = Path(
        next(row[2] for row in db.execute("PRAGMA database_list") if row[1] == "main")
    ).resolve()
    data_root = db_file.parent.parent if db_file.parent.name == "db" else db_file.parent
    snapshot = (
        data_root
        / "apps"
        / "learning"
        / "university-source"
        / "objects"
        / "sha256"
        / content_hash[:2]
        / f"{content_hash}.txt"
    )
    snapshot.parent.mkdir(parents=True, exist_ok=True)
    if snapshot.exists() and snapshot.read_bytes() != content:
        raise IngestionError(f"content-addressed snapshot is corrupt: {snapshot}")
    if not snapshot.exists():
        snapshot.write_bytes(content)
    return content_hash, snapshot


@dataclass(frozen=True)
class ChunkSpec:
    """One proposed citable excerpt. ``start_line``/``end_line`` are
    1-indexed, inclusive, and must reproduce the live file exactly --
    this is verified immediately after insert, not assumed.
    """

    start_line: int
    end_line: int


def add_source_version(
    db: sqlite3.Connection,
    *,
    course_id: str,
    source_id: str,
    file_path: Path,
    chunks: list[ChunkSpec],
    namespace: str = "learning",
    evidence_class: str = "SOURCE",
) -> str:
    """Idempotently register one source file (e.g. a lecture transcript)
    and its chunks. Unchanged content returns the current version; changed
    content appends a version linked through ``supersedes_id``.

    Every inserted chunk is re-validated via
    ``citations.validate_and_read_chunk`` before this function returns,
    inside the same transaction the caller controls -- an inconsistency
    (e.g. a chunk range that doesn't reproduce the file) raises
    ``IngestionError`` and the caller's transaction should be rolled back.
    """
    course_row = db.execute("SELECT 1 FROM course WHERE id = ?", (course_id,)).fetchone()
    if course_row is None:
        raise IngestionError(f"no such course: {course_id} (create it first, do not guess)")

    if not file_path.is_file():
        raise IngestionError(f"source file does not exist: {file_path}")
    raw = file_path.read_bytes()
    content_hash, snapshot = _snapshot_source(db, raw)

    latest = db.execute(
        """SELECT id, native_version, content_hash FROM source_version
            WHERE namespace=? AND source_id=?
            ORDER BY native_version DESC LIMIT 1""",
        (namespace, source_id),
    ).fetchone()
    if latest is not None and latest["content_hash"] == content_hash:
        return latest["id"]

    native_version = int(latest["native_version"] if latest else 0) + 1
    source_version_id = f"sv_{uuid.uuid4().hex}"
    captured_at = now_iso()
    db.execute(
        "INSERT INTO source_version(id, namespace, source_id, native_version, content_hash, "
        "private_locator, captured_at, processing_status, evidence_class, supersedes_id) "
        "VALUES (?,?,?,?,?,?,?,?,?,?)",
        (
            source_version_id,
            namespace,
            source_id,
            native_version,
            content_hash,
            str(snapshot),
            captured_at,
            "PROCESSED",
            evidence_class,
            latest["id"] if latest else None,
        ),
    )
    db.execute(
        "INSERT INTO reference_version(id, source_version_id, course_id, created_at) VALUES (?,?,?,?)",
        (f"ref_{uuid.uuid4().hex}", source_version_id, course_id, captured_at),
    )

    text = raw.decode("utf-8", errors="strict")
    lines = text.splitlines(keepends=True)
    for ordinal, spec in enumerate(chunks):
        if spec.start_line < 1 or spec.end_line < spec.start_line or spec.end_line > len(lines):
            raise IngestionError(
                f"chunk {ordinal} range {spec.start_line}-{spec.end_line} is out of bounds "
                f"for a {len(lines)}-line file"
            )
        excerpt = "".join(lines[spec.start_line - 1 : spec.end_line])
        chunk_id = f"chunk_{uuid.uuid4().hex}"
        citation_location = f"{source_id}:{spec.start_line}-{spec.end_line}"
        record_ref = f"{namespace}:{source_id}:v{native_version}:{ordinal}"
        db.execute(
            "INSERT INTO chunk(id, namespace, source_version_id, record_ref, ordinal, "
            "citation_location, text, token_count, tokenizer_version) VALUES (?,?,?,?,?,?,?,?,?)",
            (
                chunk_id,
                namespace,
                source_version_id,
                record_ref,
                ordinal,
                citation_location,
                excerpt,
                len(excerpt.split()),
                "whitespace-v1",
            ),
        )
        db.execute("INSERT INTO chunk_fts(rowid, text) SELECT rowid, text FROM chunk WHERE id = ?", (chunk_id,))

        try:
            validate_and_read_chunk(db, chunk_id)
        except CitationValidationError as exc:
            raise IngestionError(f"chunk {ordinal} failed immediate re-validation: {exc}") from exc

    db.execute(
        "INSERT INTO audit_event(namespace, event_type, target_ref, actor, occurred_at, details_json) "
        "VALUES (?,?,?,?,?,?)",
        (
            "learning",
            "ACADEMIA_SOURCE_INGESTED",
            source_version_id,
            "academia-lecture-ingestion",
            captured_at,
            json.dumps(
                {
                    "course_id": course_id,
                    "source_id": source_id,
                    "native_version": native_version,
                    "supersedes_id": latest["id"] if latest else None,
                    "chunk_count": len(chunks),
                },
                sort_keys=True,
            ),
        ),
    )
    return source_version_id


def add_objective(
    db: sqlite3.Connection,
    *,
    course_id: str,
    objective_id: str,
    definition: str,
    prerequisite_ids: list[str] | None = None,
    version: int = 1,
) -> str:
    """Idempotently register a new learning objective taught in a lecture.

    Prerequisite links are only ever added to objective_version ids that
    already exist -- an unknown prerequisite id raises rather than being
    silently created as a stub, since a fabricated prerequisite would
    corrupt the semantic-satisfaction check in objectives.py.
    """
    course_row = db.execute("SELECT 1 FROM course WHERE id = ?", (course_id,)).fetchone()
    if course_row is None:
        raise IngestionError(f"no such course: {course_id}")

    objective_version_id = f"{objective_id}:v{version}"
    existing: Objective | None = get_learning_objective(db, objective_version_id)
    if existing is not None:
        return objective_version_id

    db.execute(
        "INSERT INTO objective_version(id, objective_id, course_id, version, definition) VALUES (?,?,?,?,?)",
        (objective_version_id, objective_id, course_id, version, definition),
    )
    for prereq_id in prerequisite_ids or []:
        prereq_row = db.execute(
            "SELECT 1 FROM objective_version WHERE id = ?", (prereq_id,)
        ).fetchone()
        if prereq_row is None:
            raise IngestionError(
                f"prerequisite {prereq_id!r} does not exist -- register it first, "
                "do not fabricate a stub prerequisite"
            )
        db.execute(
            "INSERT INTO prerequisite(objective_version_id, prerequisite_version_id) VALUES (?,?)",
            (objective_version_id, prereq_id),
        )

    db.execute(
        "INSERT INTO audit_event(namespace, event_type, target_ref, actor, occurred_at, details_json) "
        "VALUES (?,?,?,?,?,?)",
        (
            "learning",
            "ACADEMIA_OBJECTIVE_INGESTED",
            objective_version_id,
            "academia-lecture-ingestion",
            now_iso(),
            f'{{"course_id":"{course_id}"}}',
        ),
    )
    return objective_version_id
