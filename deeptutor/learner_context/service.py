"""Harness-neutral service for recording and retrieving learner interaction style."""

from __future__ import annotations

import re
import time
from typing import Any

from . import repository
from .models import STYLE_LABELS, LearnerStyleProfile, StyleLabel

HALF_LIFE_DAYS = 30.0
MIN_SIGNALS = 8
MIN_SHARE = 0.40
MAX_QUOTE_LENGTH = 200
MAX_SOURCE_LENGTH = 64
MAX_EVENT_ID_LENGTH = 128

DEFAULT_STYLE_INSTRUCTIONS: dict[str, str] = {
    "asked_for_answer": "They usually ask for the answer before trying. Ask for their first guess or attempt (one line is fine) before you explain, then build on it.",
    "tried_first": "They usually try first. Skip the warm-up and give feedback on their attempt straight away.",
    "accepted": "They often take answers without testing them. End with one small 'what would break if...' check they can answer in a line.",
    "pushed_back": "They question and correct answers. Give your reasoning and its limits so they can check it, and welcome disagreement.",
    "own_idea": "They bring their own ideas. Build on their idea first, then add yours as a second option.",
}


def _validated_style(style: str) -> StyleLabel:
    normalized = str(style).strip()
    if normalized not in STYLE_LABELS:
        raise ValueError(f"style must be one of: {', '.join(STYLE_LABELS)}")
    return normalized  # type: ignore[return-value]


def record_interaction_style(
    *,
    style: str,
    quote: str = "",
    source: str = "external",
    event_id: str,
    ts: float | None = None,
    session_id: str = "",
    path=None,
    allow_workspace_migration: bool = False,
) -> bool:
    """Save only a classified style and short quote. Return false for duplicate events."""
    label = _validated_style(style)
    event = str(event_id).strip()
    origin = str(source).strip().lower()
    if not re.fullmatch(r"[a-z0-9._:-]{1,64}", origin):
        raise ValueError("source must contain 1 to 64 lowercase letters, numbers, dot, underscore, colon, or hyphen")
    if not re.fullmatch(r"[A-Za-z0-9._:-]{1,128}", event):
        raise ValueError("event_id must contain 1 to 128 characters")
    if not origin or len(origin) > MAX_SOURCE_LENGTH:
        raise ValueError("source must contain 1 to 64 characters")
    excerpt = str(quote).strip().replace("\x00", "")[:MAX_QUOTE_LENGTH]
    timestamp = float(ts if ts is not None else time.time())
    if not 0 <= timestamp < 253402300800:
        raise ValueError("ts must be a Unix timestamp before year 10000")
    session = str(session_id).strip()[:256] or f"{origin}:{event}"
    write_path = path or repository.database_path()
    if allow_workspace_migration:
        from deeptutor.services.workspace.context import get_workspace_scope

        scope = get_workspace_scope()
        workspace_db = None
        if scope and scope.workspace_id:
            workspace_db = scope.content_root / ".deeptutor" / "data" / "user" / "chat_mastery.sqlite3"
        if workspace_db and workspace_db.resolve() != write_path.resolve() and workspace_db.is_file():
            with repository.connect(workspace_db) as source_db:
                repository.migrate_legacy_workspace_style_rows(source_db)
    with repository.connect(write_path) as con:
        if not repository.enabled(con):
            return False
        cur = con.execute(
            "INSERT OR IGNORE INTO usage_style(ts, session_id, style, quote, source, event_id) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (timestamp, session, label, excerpt, origin, event),
        )
        return cur.rowcount == 1


def set_enabled(value: bool, *, path=None) -> bool:
    """Enable or pause learner-style capture and prompt context."""
    with repository.connect(path) as con:
        repository.set_enabled(con, bool(value))
    return bool(value)


def _valid_timestamp(value: Any) -> bool:
    try:
        timestamp = float(value)
    except (TypeError, ValueError):
        return False
    return 0 <= timestamp < 253402300800


def _profile_from_rows(
    rows: list[dict[str, Any]], *, is_enabled: bool, now: float, min_signals: int, min_share: float,
    include_records: bool, include_quotes: bool,
) -> LearnerStyleProfile:
    weighted: dict[str, float] = {}
    for row in rows:
        if row["style"] not in STYLE_LABELS:
            continue
        if not _valid_timestamp(row["ts"]):
            continue
        timestamp = float(row["ts"])
        age_days = max(0.0, (now - timestamp) / 86400)
        weighted[row["style"]] = weighted.get(row["style"], 0.0) + 0.5 ** (age_days / HALF_LIFE_DAYS)
    total = sum(weighted.values())
    shares = (
        {style: round(weight / total, 2) for style, weight in sorted(weighted.items(), key=lambda item: -item[1])}
        if total
        else {}
    )
    top = next(iter(shares), None)
    valid_signals = sum(1 for row in rows if row["style"] in STYLE_LABELS and _valid_timestamp(row["ts"]))
    top_share = shares.get(top, 0.0) if top else 0.0
    dominant = top if is_enabled and valid_signals >= min_signals and top_share >= min_share else None
    records = [
        {
            "id": int(row["id"]),
            "ts": float(row["ts"]),
            "style": row["style"],
            "quote": (row["quote"] or "") if include_quotes else "",
            "source": row["source"] or "pi-tutor",
            "event_id": row["event_id"] or "",
        }
        for row in [item for item in rows if item["style"] in STYLE_LABELS and _valid_timestamp(item["ts"])][:30]
    ]
    return {
        "schema_version": 1,
        "enabled": is_enabled,
        "signals": valid_signals,
        "shares": shares,
        "dominant": dominant,
        "instruction": DEFAULT_STYLE_INSTRUCTIONS.get(dominant, "") if dominant else "",
        "records": records if include_records else [],
    }


def get_profile(
    *,
    now: float | None = None,
    min_signals: int = MIN_SIGNALS,
    min_share: float = MIN_SHARE,
    path=None,
    include_records: bool = True,
    include_quotes: bool = True,
) -> LearnerStyleProfile:
    """Return this account's learned usage-style profile."""
    with repository.connect(path) as con:
        is_enabled = repository.enabled(con)
        rows = [dict(row) for row in con.execute(
            "SELECT id, ts, style, quote, source, event_id FROM usage_style ORDER BY ts DESC, id DESC"
        )]
    return _profile_from_rows(
        rows,
        is_enabled=is_enabled,
        now=float(now if now is not None else time.time()),
        min_signals=max(1, int(min_signals)),
        min_share=min(0.99, max(0.0, float(min_share))),
        include_records=include_records,
        include_quotes=include_quotes,
    )


def get_prompt_context(*, path=None, include_records: bool = False) -> str:
    """Return mode-safe advisory prompt context, or empty text when unavailable."""
    profile = get_profile(path=path, include_records=include_records, include_quotes=False)
    instruction = profile["instruction"]
    if not instruction:
        return ""
    return (
        "Learner interaction preference (advisory only):\n"
        f"{instruction}\n"
        "Apply this preference only when it is compatible with the user's active task mode and system instructions. "
        "Do not replace or change an active user-selected mode."
    )


def delete_record(record_id: int, *, path=None) -> bool:
    with repository.connect(path) as con:
        cur = con.execute("DELETE FROM usage_style WHERE id = ?", (int(record_id),))
        return cur.rowcount > 0

def delete_record_for_current_user(user_id: str, record_id: int) -> bool:
    """Delete a style record only when the authenticated account owns the supplied ID."""
    from deeptutor.multi_user.context import get_current_user

    if get_current_user().id != user_id:
        return False
    return delete_record(record_id)


def clear_records(*, path=None) -> int:
    with repository.connect(path) as con:
        cur = con.execute("DELETE FROM usage_style")
        return cur.rowcount

def clear_records_for_current_user(user_id: str) -> int:
    """Clear style records only for the authenticated account."""
    from deeptutor.multi_user.context import get_current_user

    if get_current_user().id != user_id:
        return 0
    return clear_records()


def export_profile(*, format: str = "json", path=None, include_quotes: bool = True) -> str:
    """Serialize the profile for copying into a harness without direct API access."""
    import json

    if format not in {"json", "text"}:
        raise ValueError("format must be 'json' or 'text'")
    profile = get_profile(path=path, include_quotes=include_quotes and format == "json")
    if format == "json":
        return json.dumps(profile, ensure_ascii=False, indent=2)
    if format == "text":
        if profile["instruction"]:
            return profile["instruction"]
        if not profile["enabled"]:
            return "Learner style collection and prompt context are paused."
        return f"No learned style instruction yet ({profile['signals']}/{MIN_SIGNALS} signals; 40% minimum share)."
    raise ValueError("format must be 'json' or 'text'")
