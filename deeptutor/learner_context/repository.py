"""SQLite adapter for the harness-neutral learner context."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import logging
import os
from pathlib import Path
import sqlite3
from typing import Iterator

logger = logging.getLogger(__name__)


def database_path() -> Path:
    """Return the account-scoped mastery database path, across content workspaces."""
    override = os.environ.get("PI_TUTOR_LEARNER_CONTEXT_DB")
    if override:
        return Path(override).expanduser()
    from deeptutor.multi_user.paths import get_account_path_service

    return get_account_path_service().user_data_dir / "chat_mastery.sqlite3"


@contextmanager
def connect(path: Path | None = None) -> Iterator[sqlite3.Connection]:
    db_path = path or database_path()
    db_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    con = sqlite3.connect(db_path, timeout=15)
    try:
        db_path.chmod(0o600)
    except OSError:
        pass
    con.execute("PRAGMA busy_timeout = 15000")
    con.row_factory = sqlite3.Row
    try:
        initialize(con)
        yield con
        con.commit()
    except BaseException:
        con.rollback()
        raise
    finally:
        con.close()


def initialize(con: sqlite3.Connection) -> None:
    """Apply only additive schema changes. Existing style rows remain valid."""
    con.execute(
        "CREATE TABLE IF NOT EXISTS usage_style ("
        "id INTEGER PRIMARY KEY, ts REAL, session_id TEXT, style TEXT, quote TEXT)"
    )
    columns = {row[1] for row in con.execute("PRAGMA table_info(usage_style)")}
    if "source" not in columns:
        con.execute("ALTER TABLE usage_style ADD COLUMN source TEXT NOT NULL DEFAULT 'pi-tutor'")
    if "event_id" not in columns:
        con.execute("ALTER TABLE usage_style ADD COLUMN event_id TEXT NOT NULL DEFAULT ''")
    con.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS usage_style_source_event_unique "
        "ON usage_style(source, event_id) WHERE event_id != ''"
    )
    con.execute(
        "CREATE TABLE IF NOT EXISTS learner_context_settings ("
        "key TEXT PRIMARY KEY, value TEXT NOT NULL)"
    )
    con.execute(
        "CREATE TABLE IF NOT EXISTS learner_context_schema_migrations ("
        "version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)"
    )
    con.execute(
        "INSERT OR IGNORE INTO learner_context_schema_migrations(version, applied_at) "
        "VALUES (1, ?)",
        (datetime.now(timezone.utc).isoformat(),),
    )
    con.execute(
        "CREATE TABLE IF NOT EXISTS learner_context_legacy_imports ("
        "source_path TEXT PRIMARY KEY, imported_at TEXT NOT NULL, imported_rows INTEGER NOT NULL)"
    )
    con.execute(
        "INSERT OR IGNORE INTO learner_context_settings(key, value) VALUES ('enabled', '1')"
    )


def migrate_legacy_workspace_style_rows(source: sqlite3.Connection) -> int:
    """Copy old content-workspace style rows into account storage without removing the originals."""
    target_path = database_path().resolve()
    source_path_row = source.execute("PRAGMA database_list").fetchone()
    source_path = Path(source_path_row[2]).resolve() if source_path_row and source_path_row[2] else None
    if source_path is None or source_path == target_path or not source_path.is_file():
        return 0
    tables = {row[0] for row in source.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if "usage_style" not in tables:
        return 0
    with connect(target_path) as target:
        already_imported = target.execute(
            "SELECT 1 FROM learner_context_legacy_imports WHERE source_path = ?", (str(source_path),)
        ).fetchone()
        if already_imported:
            return 0
        source_columns = {row[1] for row in source.execute("PRAGMA table_info(usage_style)")}
        if not {"id", "ts", "session_id", "style", "quote"} <= source_columns:
            return 0
        if {"source", "event_id"} <= source_columns:
            rows = source.execute(
                "SELECT id, ts, session_id, style, quote, source, event_id FROM usage_style ORDER BY id"
            ).fetchall()
        else:
            rows = source.execute(
                "SELECT id, ts, session_id, style, quote FROM usage_style ORDER BY id"
            ).fetchall()
        digest = hashlib.sha256(str(source_path).encode()).hexdigest()[:16]
        imported = 0
        for row in rows:
            row_id, ts, session_id, style, quote = row[:5]
            event_id = str(row[6]) if len(row) > 6 and row[6] else f"legacy-{digest}-{row_id}"
            source_name = str(row[5]) if len(row) > 6 and row[5] else "legacy-import"
            cursor = target.execute(
                "INSERT OR IGNORE INTO usage_style(ts, session_id, style, quote, source, event_id) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (ts, session_id, style, quote, source_name, event_id),
            )
            imported += cursor.rowcount
        target.execute(
            "INSERT INTO learner_context_legacy_imports(source_path, imported_at, imported_rows) VALUES (?, ?, ?)",
            (str(source_path), datetime.now(timezone.utc).isoformat(), imported),
        )
        logger.info("Copied %d learner style rows to account store from %s", imported, source_path)
        return imported


def enabled(con: sqlite3.Connection) -> bool:
    row = con.execute(
        "SELECT value FROM learner_context_settings WHERE key = 'enabled'"
    ).fetchone()
    return row is None or row[0] != "0"


def set_enabled(con: sqlite3.Connection, value: bool) -> None:
    con.execute(
        "INSERT INTO learner_context_settings(key, value) VALUES ('enabled', ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        ("1" if value else "0",),
    )
