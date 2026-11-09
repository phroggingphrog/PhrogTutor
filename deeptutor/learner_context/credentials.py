"""Revocable per-owner learner-context credentials.

Credential hashes live in an owner-scoped SQLite database. Token values live in
Pi Tutor's existing owner secret store and never appear in command output.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import os
from pathlib import Path
import re
import secrets
import sqlite3
import threading
from typing import Any

_write_lock = threading.Lock()
_OWNER_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_ID_RE = re.compile(r"^[a-f0-9]{24}$")


def current_owner_id() -> str:
    from deeptutor.multi_user.context import get_current_user

    return get_current_user().id


def database_path(owner_id: str | None = None) -> Path:
    """Return a private credential database for one account identity."""
    owner = str(owner_id or current_owner_id())
    if not _OWNER_RE.fullmatch(owner):
        raise ValueError("Invalid learner-context credential owner")
    override = os.environ.get("PI_TUTOR_LEARNER_CONTEXT_CREDENTIALS_DB")
    if override:
        root = Path(override).expanduser().parent / "learner-context" / "credentials"
    else:
        from deeptutor.multi_user.paths import SYSTEM_ROOT

        root = SYSTEM_ROOT / "auth" / "learner-context" / "credentials"
    return root / f"{owner}.sqlite3"


def _secret_store():
    from deeptutor.services.mcp import secrets as store

    return store


def _connect(owner_id: str | None = None) -> sqlite3.Connection:
    path = database_path(owner_id)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        path.parent.chmod(0o700)
    except OSError:
        pass
    con = sqlite3.connect(path, timeout=10)
    con.row_factory = sqlite3.Row
    con.execute(
        "CREATE TABLE IF NOT EXISTS credentials ("
        "id TEXT PRIMARY KEY, token_hash TEXT NOT NULL UNIQUE, user_id TEXT NOT NULL, "
        "name TEXT NOT NULL, scopes TEXT NOT NULL, created_at TEXT NOT NULL, "
        "expires_at TEXT NOT NULL, revoked_at TEXT)"
    )
    try:
        path.chmod(0o600)
    except OSError:
        pass
    con.commit()
    return con


def _now() -> datetime:
    return datetime.now(timezone.utc)


def create(
    user_id: str,
    name: str,
    expires_in_days: int,
    scopes: tuple[str, ...] = ("read", "write"),
    *,
    reveal: bool = False,
) -> dict[str, str]:
    """Create a token for the current owner and store its value in the secret vault."""
    owner = current_owner_id()
    if str(user_id) != owner:
        raise ValueError("Credential owner must match the current account")
    if not _OWNER_RE.fullmatch(owner):
        raise ValueError("Invalid learner-context credential owner")
    if not 1 <= int(expires_in_days) <= 90:
        raise ValueError("expires_in_days must be between 1 and 90")
    label = str(name).strip()
    if not label or len(label) > 80 or any(ord(char) < 32 for char in label):
        raise ValueError("name must contain 1 to 80 printable characters")
    allowed = set(scopes)
    if not allowed or not allowed <= {"read", "write"}:
        raise ValueError("scopes must contain read and/or write")

    scope_value = ",".join(sorted(allowed))
    now = _now()
    expires = now + timedelta(days=int(expires_in_days))
    credential_id = secrets.token_hex(12)
    token = f"dtlc_{owner}.{credential_id}.{secrets.token_urlsafe(32)}"
    with _write_lock:
        con = _connect(owner)
        try:
            con.execute(
                "INSERT INTO credentials(id, token_hash, user_id, name, scopes, created_at, expires_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    credential_id,
                    hashlib.sha256(token.encode()).hexdigest(),
                    owner,
                    label,
                    scope_value,
                    now.isoformat(),
                    expires.isoformat(),
                ),
            )
            con.commit()
        finally:
            con.close()
        try:
            existing = _secret_store().load_secret_values(owner, "learner-context")
            existing[credential_id] = token
            _secret_store().store_secrets(owner, "learner-context", existing)
        except Exception:
            con = _connect(owner)
            try:
                con.execute("DELETE FROM credentials WHERE id = ?", (credential_id,))
                con.commit()
            finally:
                con.close()
            raise

    return {
        "id": credential_id,
        "access_token": (_secret_store().load_secret_value(owner, "learner-context", credential_id) or "") if reveal else "",
        "secret_id": credential_id,
        "name": label,
        "scopes": scope_value,
        "secret_reference": f"${{secret:learner-context/{credential_id}}}",
        "created_at": now.isoformat(),
        "expires_at": expires.isoformat(),
    }


def authenticate(token: str, *, owner_id: str | None = None) -> dict[str, Any] | None:
    """Validate the bearer token against its signed-in owner's row and secret vault."""
    if not isinstance(token, str) or not token.startswith("dtlc_") or len(token) > 256:
        return None
    parts = token[5:].split(".", 2)
    if len(parts) != 3:
        return None
    token_owner, credential_id, random_part = parts
    if not _OWNER_RE.fullmatch(token_owner) or not _ID_RE.fullmatch(credential_id) or not random_part:
        return None
    if owner_id is None:
        return None
    expected_owner = str(owner_id)
    if token_owner != expected_owner:
        return None
    if not database_path(expected_owner).is_file():
        return None

    digest = hashlib.sha256(token.encode()).hexdigest()
    con = _connect(expected_owner)
    try:
        row = con.execute(
            "SELECT id, user_id, name, scopes, expires_at, revoked_at, token_hash "
            "FROM credentials WHERE id = ? AND user_id = ?",
            (credential_id, expected_owner),
        ).fetchone()
    finally:
        con.close()
    if row is None or row["revoked_at"]:
        return None
    if not hmac.compare_digest(str(row["token_hash"]), digest):
        return None
    stored = _secret_store().load_secret_value(expected_owner, "learner-context", credential_id)
    if not stored or not hmac.compare_digest(stored, token):
        return None
    try:
        expiry = datetime.fromisoformat(row["expires_at"])
    except (TypeError, ValueError):
        return None
    if expiry.tzinfo is None:
        expiry = expiry.replace(tzinfo=timezone.utc)
    if expiry <= _now():
        return None
    return {key: row[key] for key in ("id", "user_id", "name", "scopes", "expires_at", "revoked_at")}


def load_token(owner_id: str, credential_id: str) -> str | None:
    """Return an unexpired token for the current local owner only."""
    owner = str(owner_id)
    if owner != current_owner_id() or not _ID_RE.fullmatch(str(credential_id)):
        return None
    path = database_path(owner)
    if not path.is_file():
        return None
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=15)
    con.row_factory = sqlite3.Row
    try:
        row = con.execute(
            "SELECT expires_at, revoked_at FROM credentials WHERE id = ? AND user_id = ?",
            (credential_id, owner_id),
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
    return _secret_store().load_secret_value(owner_id, "learner-context", credential_id)


def list_for_user(user_id: str) -> list[dict[str, Any]]:
    if str(user_id) != current_owner_id():
        return []
    con = _connect(user_id)
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
    if str(user_id) != current_owner_id():
        return False
    with _write_lock:
        con = _connect(user_id)
        try:
            cur = con.execute(
                "UPDATE credentials SET revoked_at = ? WHERE id = ? AND user_id = ? AND revoked_at IS NULL",
                (_now().isoformat(), credential_id, user_id),
            )
            con.commit()
            changed = cur.rowcount > 0
        finally:
            con.close()
        if changed:
            _secret_store().store_secrets(user_id, "learner-context", {credential_id: ""})
    return changed


def revoke_all(user_id: str) -> int:
    if str(user_id) != current_owner_id():
        return 0
    with _write_lock:
        con = _connect(user_id)
        try:
            rows = con.execute(
                "SELECT id FROM credentials WHERE user_id = ? AND revoked_at IS NULL", (user_id,)
            ).fetchall()
            ids = [str(row["id"]) for row in rows]
            cur = con.execute(
                "UPDATE credentials SET revoked_at = ? WHERE user_id = ? AND revoked_at IS NULL",
                (_now().isoformat(), user_id),
            )
            con.commit()
        finally:
            con.close()
        if ids:
            _secret_store().store_secrets(
                user_id, "learner-context", {credential_id: "" for credential_id in ids}
            )
    return cur.rowcount
