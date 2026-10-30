"""Revocable, least-privilege bearer credentials for harness integrations."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import os
from pathlib import Path
import re
import secrets
import sqlite3
import threading
from typing import Any

_write_lock = threading.Lock()


def database_path() -> Path:
    override = os.environ.get("PI_TUTOR_LEARNER_CONTEXT_CREDENTIALS_DB")
    if override:
        return Path(override).expanduser()
    from deeptutor.multi_user.paths import SYSTEM_ROOT

    return SYSTEM_ROOT / "auth" / "learner_context_credentials.sqlite3"


def _connect() -> sqlite3.Connection:
    db_path = database_path()
    db_path.parent.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    db_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        db_path.parent.parent.chmod(0o700)
        db_path.parent.chmod(0o700)
    except OSError:
        pass
    con = sqlite3.connect(db_path, timeout=10)
    con.row_factory = sqlite3.Row
    try:
        db_path.chmod(0o600)
    except OSError:
        pass
    con.execute(
        "CREATE TABLE IF NOT EXISTS credentials ("
        "id TEXT PRIMARY KEY, token_hash TEXT NOT NULL UNIQUE, user_id TEXT NOT NULL, "
        "name TEXT NOT NULL, scopes TEXT NOT NULL DEFAULT 'read,write', created_at TEXT NOT NULL, "
        "expires_at TEXT NOT NULL, revoked_at TEXT)"
    )
    columns = {row[1] for row in con.execute("PRAGMA table_info(credentials)")}
    if "scopes" not in columns:
        con.execute("ALTER TABLE credentials ADD COLUMN scopes TEXT NOT NULL DEFAULT 'read,write'")
    con.commit()
    return con


def _now() -> datetime:
    return datetime.now(timezone.utc)


def create(user_id: str, name: str, expires_in_days: int, scopes: tuple[str, ...] = ("read", "write")) -> dict[str, str]:
    user_id = str(user_id).strip()
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", user_id):
        raise ValueError("user_id must contain 1 to 64 letters, numbers, underscores, or hyphens")
    if not 1 <= int(expires_in_days) <= 90:
        raise ValueError("expires_in_days must be between 1 and 90")
    label = str(name).strip()
    if not label or len(label) > 80:
        raise ValueError("name must contain 1 to 80 characters")
    if any(ord(char) < 32 for char in label):
        raise ValueError("name cannot contain control characters")
    allowed = set(scopes)
    if not allowed or not allowed <= {"read", "write"}:
        raise ValueError("scopes must contain read and/or write")
    scope_value = ",".join(sorted(allowed))
    token = "dtlc_" + secrets.token_urlsafe(32)
    now = _now()
    expires = now + timedelta(days=int(expires_in_days))
    row_id = secrets.token_hex(12)
    with _write_lock:
        con = _connect()
        try:
            con.execute(
                "INSERT INTO credentials(id, token_hash, user_id, name, scopes, created_at, expires_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (row_id, hashlib.sha256(token.encode()).hexdigest(), user_id, label, scope_value, now.isoformat(), expires.isoformat()),
            )
            con.commit()
        finally:
            con.close()
    return {
        "id": row_id,
        "access_token": token,
        "name": label,
        "scopes": scope_value,
        "created_at": now.isoformat(),
        "expires_at": expires.isoformat(),
    }


def authenticate(token: str) -> dict[str, Any] | None:
    if not isinstance(token, str) or not token.startswith("dtlc_") or len(token) > 256:
        return None
    digest = hashlib.sha256(token.encode()).hexdigest()
    con = _connect()
    try:
        row = con.execute(
            "SELECT id, user_id, name, scopes, expires_at, revoked_at FROM credentials WHERE token_hash = ?",
            (digest,),
        ).fetchone()
    finally:
        con.close()
    if row is None or row["revoked_at"]:
        return None
    try:
        expiry = datetime.fromisoformat(row["expires_at"])
    except (TypeError, ValueError):
        return None
    if expiry.tzinfo is None:
        expiry = expiry.replace(tzinfo=timezone.utc)
    if expiry <= _now():
        return None
    return dict(row)


def list_for_user(user_id: str) -> list[dict[str, Any]]:
    con = _connect()
    try:
        rows = con.execute(
            "SELECT id, name, scopes, created_at, expires_at, revoked_at FROM credentials "
            "WHERE user_id = ? ORDER BY created_at DESC",
            (user_id,),
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        con.close()


def revoke(user_id: str, credential_id: str) -> bool:
    with _write_lock:
        con = _connect()
        try:
            cur = con.execute(
                "UPDATE credentials SET revoked_at = ? WHERE id = ? AND user_id = ? AND revoked_at IS NULL",
                (_now().isoformat(), credential_id, user_id),
            )
            con.commit()
            return cur.rowcount > 0
        finally:
            con.close()


def revoke_all(user_id: str) -> int:
    with _write_lock:
        con = _connect()
        try:
            cur = con.execute(
                "UPDATE credentials SET revoked_at = ? WHERE user_id = ? AND revoked_at IS NULL",
                (_now().isoformat(), user_id),
            )
            con.commit()
            return cur.rowcount
        finally:
            con.close()
