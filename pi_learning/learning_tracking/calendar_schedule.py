"""Calendar review scheduling adapter for spaced review blocks and study sessions.

Generates RFC 5545 compliant iCalendar (.ics) exports and Google/Apple Calendar
payloads for FSRS reviews and Canvas learning blocks. Enforces strict study
windows, session durations, and privacy sanitization to prevent personal
calendar leakage.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
import os
import json
from pathlib import Path
import re
from typing import Any, Dict, List, Optional, Sequence
import uuid

# RFC 5545 Text Escaping & Sanitization
_EMAIL_PATTERN = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b")
_SECRET_PATTERN = re.compile(
    r"(Bearer\s+[A-Za-z0-9._~+/-]+|Basic\s+[A-Za-z0-9._~+/=-]+|ghp_[A-Za-z0-9]+|sk-[A-Za-z0-9]+|token=[^&\s]+|password=[^&\s]+|secret=[^&\s]+|key=[^&\s]+)",
    re.IGNORECASE,
)
_PRIVATE_PATH_PATTERN = re.compile(
    r"(/home/[^/\s]+/(\.ssh|\.gnupg|\.hermes|\.aws|\.config/gcloud)[^/\s]*|[~A-Za-z0-9_./-]*\.hermes[^\s]*)",
    re.IGNORECASE,
)


def sanitize_calendar_text(text: str) -> str:
    """Sanitize text to prevent personal data or secret leakage in calendar events."""
    if not text:
        return ""
    sanitized = _EMAIL_PATTERN.sub("[REDACTED_EMAIL]", text)
    sanitized = _SECRET_PATTERN.sub("[REDACTED_SECRET]", sanitized)
    sanitized = _PRIVATE_PATH_PATTERN.sub("[REDACTED_PATH]", sanitized)
    return sanitized.strip()


def _escape_ics_text(text: str) -> str:
    """Escape text for RFC 5545 compliance."""
    if not text:
        return ""
    # Backslash, semicolon, comma, and newline escaping
    escaped = text.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,")
    escaped = escaped.replace("\r\n", "\\n").replace("\n", "\\n").replace("\r", "\\n")
    return escaped


def _fold_ics_line(line: str, max_octets: int = 75) -> str:
    """Fold lines exceeding max_octets according to RFC 5545."""
    encoded = line.encode("utf-8")
    if len(encoded) <= max_octets:
        return line

    chunks: List[bytes] = []
    curr = encoded
    while len(curr) > max_octets:
        # Split at max_octets boundary
        split_point = max_octets
        # Avoid splitting multi-byte UTF-8 sequences
        while split_point > 0 and (curr[split_point] & 0xC0) == 0x80:
            split_point -= 1
        chunks.append(curr[:split_point])
        curr = b" " + curr[split_point:]  # Indent continuation line with space
    if curr:
        chunks.append(curr)

    return "\r\n".join(c.decode("utf-8") for c in chunks)


@dataclass(frozen=True)
class StudyWindow:
    start_hour: int = 18
    start_minute: int = 0
    end_hour: int = 21
    end_minute: int = 0
    session_duration_minutes: int = 30
    break_minutes: int = 5
    max_sessions_per_day: int = 4

    def window_start_time(self) -> time:
        return time(self.start_hour, self.start_minute)

    def window_end_time(self) -> time:
        return time(self.end_hour, self.end_minute)


@dataclass(frozen=True)
class CalendarEvent:
    uid: str
    summary: str
    description: str
    start_time: datetime
    end_time: datetime
    categories: tuple[str, ...] = ("STUDY", "REVIEW", "LEARNING")
    location: str = "Hermes Study Desk"
    status: str = "CONFIRMED"

    def duration_minutes(self) -> int:
        return int((self.end_time - self.start_time).total_seconds() // 60)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "uid": self.uid,
            "summary": self.summary,
            "description": self.description,
            "start_time": self.start_time.isoformat(),
            "end_time": self.end_time.isoformat(),
            "duration_minutes": self.duration_minutes(),
            "categories": list(self.categories),
            "location": self.location,
            "status": self.status,
        }

    def to_google_payload(self) -> Dict[str, Any]:
        """Generate Google Calendar API event resource dictionary.

        Event ID must be valid base32hex (only lowercase a-v and 0-9, length 5-1024 chars).
        Deterministic SHA-256 hex digest (32 chars) strictly satisfies this.
        """
        import hashlib
        google_id = hashlib.sha256(self.uid.encode("utf-8")).hexdigest()[:32]
        return {
            "id": google_id,
            "summary": self.summary,
            "description": self.description,
            "location": self.location,
            "status": self.status.lower(),
            "start": {
                "dateTime": self.start_time.isoformat(),
                "timeZone": str(self.start_time.tzinfo or "UTC"),
            },
            "end": {
                "dateTime": self.end_time.isoformat(),
                "timeZone": str(self.end_time.tzinfo or "UTC"),
            },
        }

    def to_apple_payload(self) -> Dict[str, Any]:
        """Generate Apple Calendar / EventKit compatible event dictionary."""
        return {
            "title": self.summary,
            "notes": self.description,
            "location": self.location,
            "startDate": self.start_time.isoformat(),
            "endDate": self.end_time.isoformat(),
            "availability": "busy",
        }

    def to_ics_vevent(self, dtstamp: Optional[datetime] = None) -> str:
        """Render standard RFC 5545 VEVENT string."""
        stamp = dtstamp or datetime.now(timezone.utc)
        stamp_str = stamp.strftime("%Y%m%dT%H%M%SZ")
        
        # Format start and end in UTC or local
        start_utc = self.start_time.astimezone(timezone.utc) if self.start_time.tzinfo else self.start_time.replace(tzinfo=timezone.utc)
        end_utc = self.end_time.astimezone(timezone.utc) if self.end_time.tzinfo else self.end_time.replace(tzinfo=timezone.utc)
        
        start_str = start_utc.strftime("%Y%m%dT%H%M%SZ")
        end_str = end_utc.strftime("%Y%m%dT%H%M%SZ")

        lines = [
            "BEGIN:VEVENT",
            f"UID:{self.uid}",
            f"DTSTAMP:{stamp_str}",
            f"DTSTART:{start_str}",
            f"DTEND:{end_str}",
            f"SUMMARY:{_escape_ics_text(self.summary)}",
            f"DESCRIPTION:{_escape_ics_text(self.description)}",
            f"CATEGORIES:{_escape_ics_text(','.join(self.categories))}",
            f"LOCATION:{_escape_ics_text(self.location)}",
            f"STATUS:{self.status}",
            "END:VEVENT",
        ]
        return "\r\n".join(_fold_ics_line(line) for line in lines)


def schedule_study_sessions(
    items: Sequence[Dict[str, Any] | Any],
    *,
    start_date: Optional[datetime | date] = None,
    study_window: Optional[StudyWindow] = None,
    default_duration_minutes: int = 30,
) -> List[CalendarEvent]:
    """Schedule spaced review blocks or study items into calendar events within the study window."""
    window = study_window or StudyWindow(session_duration_minutes=default_duration_minutes)
    
    if start_date is None:
        curr_dt = datetime.now(timezone.utc)
    elif isinstance(start_date, date) and not isinstance(start_date, datetime):
        curr_dt = datetime.combine(start_date, window.window_start_time(), tzinfo=timezone.utc)
    else:
        curr_dt = start_date.astimezone(timezone.utc) if start_date.tzinfo else start_date.replace(tzinfo=timezone.utc)

    # Align starting point to study window
    current_day = curr_dt.date()
    current_slot_start = datetime.combine(current_day, window.window_start_time(), tzinfo=timezone.utc)
    if curr_dt > current_slot_start:
        # If past today's start, start either at next slot or next day
        day_end = datetime.combine(current_day, window.window_end_time(), tzinfo=timezone.utc)
        if curr_dt + timedelta(minutes=window.session_duration_minutes) > day_end:
            current_day += timedelta(days=1)
            current_slot_start = datetime.combine(current_day, window.window_start_time(), tzinfo=timezone.utc)
        else:
            current_slot_start = curr_dt

    events: List[CalendarEvent] = []
    sessions_today = 0

    for item in items:
        # Extract title and description
        if isinstance(item, dict):
            # Review item, study block, or assignment dict
            summary_raw = (
                item.get("summary")
                or item.get("title")
                or (f"Review: {item.get('objective_version_id', 'Concept')}" if "objective_version_id" in item else "Study Session")
            )
            desc_parts = []
            if "block" in item and isinstance(item["block"], dict):
                b = item["block"]
                if "learner_problem" in b:
                    desc_parts.append(f"Task: {b['learner_problem']}")
                if "artifact_check" in b:
                    desc_parts.append(f"Artifact Check: {b['artifact_check']}")
                if "understanding_test" in b:
                    desc_parts.append(f"Verification: {b['understanding_test']}")
            elif "exercise" in item:
                ex = item["exercise"]
                desc_parts.append(f"Exercise: {ex.get('task', '')}")
                if "verification" in item:
                    desc_parts.append(f"Verification: {item['verification'].get('command', '')}")
            else:
                desc_parts.append(item.get("description", "Spaced retrieval and concept review."))

            desc_raw = "\n".join(desc_parts)
            target_time = item.get("next_review_at") or item.get("personal_final") or item.get("due_at")
        else:
            summary_raw = getattr(item, "title", str(item))
            desc_raw = getattr(item, "description", "Study Session")
            target_time = getattr(item, "personal_final", getattr(item, "due_at", None))

        # Check if item has a specific scheduled target time in the future
        if target_time:
            if isinstance(target_time, str):
                try:
                    target_dt = datetime.fromisoformat(target_time)
                except Exception:
                    target_dt = None
            elif isinstance(target_time, datetime):
                target_dt = target_time
            else:
                target_dt = None

            if target_dt and target_dt.date() > current_day:
                current_day = target_dt.date()
                current_slot_start = datetime.combine(current_day, window.window_start_time(), tzinfo=timezone.utc)
                sessions_today = 0

        # Check if daily window capacity reached
        day_window_end = datetime.combine(current_day, window.window_end_time(), tzinfo=timezone.utc)
        slot_end = current_slot_start + timedelta(minutes=window.session_duration_minutes)

        if slot_end > day_window_end or sessions_today >= window.max_sessions_per_day:
            # Advance to next day's window
            current_day += timedelta(days=1)
            current_slot_start = datetime.combine(current_day, window.window_start_time(), tzinfo=timezone.utc)
            slot_end = current_slot_start + timedelta(minutes=window.session_duration_minutes)
            sessions_today = 0

        event_uid = f"study-{uuid.uuid4().hex[:12]}@learning-tracking.hermes"
        event = CalendarEvent(
            uid=event_uid,
            summary=sanitize_calendar_text(summary_raw),
            description=sanitize_calendar_text(desc_raw),
            start_time=current_slot_start,
            end_time=slot_end,
            categories=("STUDY", "REVIEW", "LEARNING"),
        )
        events.append(event)
        sessions_today += 1

        # Advance slot for next item with break
        current_slot_start = slot_end + timedelta(minutes=window.break_minutes)

    return events


def export_icalendar(
    events: Sequence[CalendarEvent],
    prod_id: str = "-//Hermes Life OS//Learning Tracking Review Scheduler//EN",
) -> str:
    """Render full RFC 5545 iCalendar (.ics) string from calendar events."""
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        f"PRODID:{prod_id}",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
    ]
    for event in events:
        lines.append(event.to_ics_vevent())
    lines.append("END:VCALENDAR")
    return "\r\n".join(lines) + "\r\n"


def write_icalendar(
    events: Sequence[CalendarEvent],
    output_path: Path | str,
    prod_id: str = "-//Hermes Life OS//Learning Tracking Review Scheduler//EN",
) -> Path:
    """Write events to an iCalendar (.ics) file."""
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    content = export_icalendar(events, prod_id=prod_id)
    path.write_bytes(content.encode("utf-8"))
    return path



@dataclass(frozen=True)
class CalendarProviderStatus:
    google_available: bool
    google_source: Optional[str]
    caldav_available: bool
    caldav_source: Optional[str]
    actionable_message: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "google_available": self.google_available,
            "google_source": self.google_source,
            "caldav_available": self.caldav_available,
            "caldav_source": self.caldav_source,
            "actionable_message": self.actionable_message,
        }


def get_calendar_provider_status(
    *,
    config: Optional[Dict[str, Any]] = None,
    hermes_dir: Optional[Path] = None,
) -> CalendarProviderStatus:
    """Detect available calendar API credentials via strict presence/boolean checks.

    NEVER prints, logs, or returns raw credential secrets, keys, or token values.
    Only returns boolean presence and safe configuration source descriptors.
    Resolves strictly through standard environment variables or explicit injected config.
    """
    cfg = config or {}

    google_available = False
    google_source: Optional[str] = None

    if cfg.get("GOOGLE_CALENDAR_ACCESS_TOKEN") or cfg.get("google_calendar_access_token") or cfg.get("access_token"):
        google_available = True
        google_source = "config:GOOGLE_CALENDAR_ACCESS_TOKEN"
    elif cfg.get("GOOGLE_CALENDAR_CLIENT_CONFIG") or cfg.get("google_calendar_client_config") or cfg.get("client_config"):
        google_available = True
        google_source = "config:GOOGLE_CALENDAR_CLIENT_CONFIG"
    elif os.getenv("GOOGLE_CALENDAR_ACCESS_TOKEN") and os.getenv("GOOGLE_CALENDAR_ACCESS_TOKEN", "").strip():
        google_available = True
        google_source = "env:GOOGLE_CALENDAR_ACCESS_TOKEN"
    elif os.getenv("GOOGLE_CALENDAR_CLIENT_CONFIG") and os.getenv("GOOGLE_CALENDAR_CLIENT_CONFIG", "").strip():
        google_available = True
        google_source = "env:GOOGLE_CALENDAR_CLIENT_CONFIG"

    caldav_available = False
    caldav_source: Optional[str] = None

    if cfg.get("CALDAV_URL") or cfg.get("caldav_url"):
        caldav_available = True
        caldav_source = "config:CALDAV_URL"
    elif os.getenv("CALDAV_URL") and os.getenv("CALDAV_URL", "").strip():
        caldav_available = True
        caldav_source = "env:CALDAV_URL"

    if google_available and caldav_available:
        msg = f"Ready: Google ({google_source}) and CalDAV ({caldav_source}) configured."
    elif google_available:
        msg = f"Ready: Google Calendar configured via {google_source}."
    elif caldav_available:
        msg = f"Ready: CalDAV / iCloud configured via {caldav_source}."
    else:
        msg = (
            "No calendar API credentials configured. Set GOOGLE_CALENDAR_ACCESS_TOKEN, "
            "GOOGLE_CALENDAR_CLIENT_CONFIG, or CALDAV_URL environment variables. "
            "Offline RFC 5545 (.ics) export is fully operational for calendar subscriptions."
        )

    return CalendarProviderStatus(
        google_available=google_available,
        google_source=google_source,
        caldav_available=caldav_available,
        caldav_source=caldav_source,
        actionable_message=msg,
    )


class CalendarProvider:
    """Base class defining the calendar provider interface."""
    name: str = "base"

    def is_configured(self) -> bool:
        raise NotImplementedError

    def sync_events(
        self,
        events: Sequence[CalendarEvent],
        *,
        idempotency_prefix: str = "hermes-sync",
    ) -> Dict[str, Any]:
        raise NotImplementedError

    def read_events(
        self,
        time_min: Optional[datetime] = None,
        time_max: Optional[datetime] = None,
    ) -> List[Dict[str, Any]]:
        raise NotImplementedError


class CalendarProviderError(RuntimeError):
    """Raised when a calendar provider encounters an error during read or sync operations."""


class CalendarProviderUnconfiguredError(CalendarProviderError):
    """Raised when an operation is attempted on an unconfigured calendar provider."""


class GoogleCalendarProvider(CalendarProvider):
    """Google Calendar provider supporting live API sync via HTTP when configured."""
    name = "google"

    def __init__(
        self,
        *,
        status: Optional[CalendarProviderStatus] = None,
        config: Optional[Dict[str, Any]] = None,
        hermes_dir: Optional[Path] = None,
    ) -> None:
        self._config = config
        self._status = status or get_calendar_provider_status(config=config, hermes_dir=hermes_dir)

    def is_configured(self) -> bool:
        return self._status.google_available

    def _resolve_token(self) -> Optional[str]:
        cfg = self._config or {}
        token = (
            cfg.get("GOOGLE_CALENDAR_ACCESS_TOKEN")
            or cfg.get("google_calendar_access_token")
            or cfg.get("access_token")
        )
        if token and str(token).strip():
            return str(token).strip()

        client_cfg = (
            cfg.get("GOOGLE_CALENDAR_CLIENT_CONFIG")
            or cfg.get("google_calendar_client_config")
            or cfg.get("client_config")
        )
        if client_cfg:
            if isinstance(client_cfg, dict):
                t = client_cfg.get("access_token") or client_cfg.get("token")
                if t:
                    return str(t).strip()
            elif isinstance(client_cfg, str):
                try:
                    parsed = json.loads(client_cfg)
                    if isinstance(parsed, dict):
                        t = parsed.get("access_token") or parsed.get("token")
                        if t:
                            return str(t).strip()
                except Exception:
                    pass

        env_token = os.getenv("GOOGLE_CALENDAR_ACCESS_TOKEN")
        if env_token and env_token.strip():
            return env_token.strip()

        env_client_cfg = os.getenv("GOOGLE_CALENDAR_CLIENT_CONFIG")
        if env_client_cfg and env_client_cfg.strip():
            try:
                parsed = json.loads(env_client_cfg)
                if isinstance(parsed, dict):
                    t = parsed.get("access_token") or parsed.get("token")
                    if t:
                        return str(t).strip()
            except Exception:
                pass

        return None

    def sync_events(
        self,
        events: Sequence[CalendarEvent],
        *,
        idempotency_prefix: str = "hermes-sync",
        calendar_id: str = "primary",
        raise_if_unconfigured: bool = False,
    ) -> Dict[str, Any]:
        """Sync events to Google Calendar API or report unconfigured.

        Never fakes in-memory success when unconfigured.
        """
        if not self.is_configured():
            if raise_if_unconfigured:
                raise CalendarProviderUnconfiguredError(
                    sanitize_calendar_text("Google Calendar credentials not configured. " + self._status.actionable_message)
                )
            return {
                "provider": self.name,
                "configured": False,
                "synced_count": 0,
                "events": [],
                "status": "unconfigured",
                "error": "Google Calendar credentials not configured",
                "actionable_message": self._status.actionable_message,
            }

        import urllib.request
        import urllib.error

        token = self._resolve_token()
        if not token:
            if raise_if_unconfigured:
                raise CalendarProviderUnconfiguredError(
                    sanitize_calendar_text("Google Calendar credentials not configured. " + self._status.actionable_message)
                )
            return {
                "provider": self.name,
                "configured": False,
                "synced_count": 0,
                "events": [],
                "status": "unconfigured",
                "error": "Google Calendar OAuth access token not found",
                "actionable_message": self._status.actionable_message,
            }

        synced_records = []
        for ev in events:
            idempotency_key = f"{idempotency_prefix}:{ev.uid}"
            payload = ev.to_google_payload()
            expected_id = payload["id"]
            url = f"https://www.googleapis.com/calendar/v3/calendars/{calendar_id}/events"
            req = urllib.request.Request(
                url,
                data=json.dumps(payload).encode("utf-8"),
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                    "X-Idempotency-Key": idempotency_key,
                },
                method="POST",
            )
            try:
                with urllib.request.urlopen(req, timeout=10) as resp:
                    resp_data = json.loads(resp.read().decode("utf-8"))
                    remote_id = resp_data.get("id")
                    if not remote_id:
                        # Fallback verification of remote event ID via GET
                        get_url = f"https://www.googleapis.com/calendar/v3/calendars/{calendar_id}/events/{expected_id}"
                        get_req = urllib.request.Request(
                            get_url,
                            headers={"Authorization": f"Bearer {token}"},
                            method="GET",
                        )
                        with urllib.request.urlopen(get_req, timeout=10) as get_resp:
                            get_data = json.loads(get_resp.read().decode("utf-8"))
                            remote_id = get_data.get("id")

                    if not remote_id or remote_id != expected_id:
                        raise CalendarProviderError(
                            f"Remote event ID verification failed: expected {expected_id}, got {remote_id}"
                        )

                    synced_records.append({
                        "uid": ev.uid,
                        "idempotency_key": idempotency_key,
                        "google_id": remote_id,
                        "status": "confirmed",
                    })
            except urllib.error.HTTPError as exc:
                if exc.code == 409:
                    # HTTP 409 Conflict: verify existing event via GET
                    get_url = f"https://www.googleapis.com/calendar/v3/calendars/{calendar_id}/events/{expected_id}"
                    get_req = urllib.request.Request(
                        get_url,
                        headers={"Authorization": f"Bearer {token}"},
                        method="GET",
                    )
                    try:
                        with urllib.request.urlopen(get_req, timeout=10) as get_resp:
                            get_data = json.loads(get_resp.read().decode("utf-8"))
                            existing_id = get_data.get("id")
                            if existing_id == expected_id:
                                synced_records.append({
                                    "uid": ev.uid,
                                    "idempotency_key": idempotency_key,
                                    "google_id": existing_id,
                                    "status": "confirmed",
                                })
                                continue
                    except Exception:
                        pass

                sanitized_err = sanitize_calendar_text(str(exc))
                if token and token in sanitized_err:
                    sanitized_err = sanitized_err.replace(token, "[REDACTED_SECRET]")
                return {
                    "provider": self.name,
                    "configured": True,
                    "synced_count": len(synced_records),
                    "events": synced_records,
                    "status": "error",
                    "error": sanitized_err,
                }
            except Exception as exc:
                sanitized_err = sanitize_calendar_text(str(exc))
                if token and token in sanitized_err:
                    sanitized_err = sanitized_err.replace(token, "[REDACTED_SECRET]")
                return {
                    "provider": self.name,
                    "configured": True,
                    "synced_count": len(synced_records),
                    "events": synced_records,
                    "status": "error",
                    "error": sanitized_err,
                }

        return {
            "provider": self.name,
            "configured": True,
            "synced_count": len(synced_records),
            "events": synced_records,
            "status": "success",
        }

    def read_events(
        self,
        calendar_id: str = "primary",
        time_min: Optional[datetime] = None,
        time_max: Optional[datetime] = None,
    ) -> List[Dict[str, Any]]:
        if not self.is_configured():
            raise CalendarProviderUnconfiguredError(
                sanitize_calendar_text("Google Calendar credentials not configured. " + self._status.actionable_message)
            )
        import urllib.request
        import urllib.error
        import urllib.parse
        token = self._resolve_token()
        url = f"https://www.googleapis.com/calendar/v3/calendars/{calendar_id}/events"
        params = []
        if time_min:
            params.append(f"timeMin={urllib.parse.quote(time_min.isoformat())}")
        if time_max:
            params.append(f"timeMax={urllib.parse.quote(time_max.isoformat())}")
        if params:
            url = f"{url}?{'&'.join(params)}"
        req = urllib.request.Request(
            url,
            headers={"Authorization": f"Bearer {token or ''}"},
            method="GET",
        )
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                return data.get("items", [])
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                return []
            sanitized_err = sanitize_calendar_text(str(exc))
            if token and token in sanitized_err:
                sanitized_err = sanitized_err.replace(token, "[REDACTED_SECRET]")
            raise CalendarProviderError(f"Google Calendar read failed ({exc.code}): {sanitized_err}") from exc
        except Exception as exc:
            sanitized_err = sanitize_calendar_text(str(exc))
            if token and token in sanitized_err:
                sanitized_err = sanitized_err.replace(token, "[REDACTED_SECRET]")
            raise CalendarProviderError(f"Google Calendar read failed: {sanitized_err}") from exc


class CalDAVCalendarProvider(CalendarProvider):
    """CalDAV / Apple iCloud provider supporting HTTP PUT when configured."""
    name = "caldav"

    def __init__(
        self,
        *,
        status: Optional[CalendarProviderStatus] = None,
        config: Optional[Dict[str, Any]] = None,
        hermes_dir: Optional[Path] = None,
    ) -> None:
        self._config = config
        self._status = status or get_calendar_provider_status(config=config, hermes_dir=hermes_dir)

    def is_configured(self) -> bool:
        return self._status.caldav_available

    def _resolve_caldav_config(self) -> tuple[str, str, str]:
        cfg = self._config or {}
        caldav_url = (
            cfg.get("CALDAV_URL")
            or cfg.get("caldav_url")
            or os.getenv("CALDAV_URL")
            or ""
        ).strip()
        username = (
            cfg.get("CALDAV_USERNAME")
            or cfg.get("caldav_username")
            or os.getenv("CALDAV_USERNAME")
            or ""
        ).strip()
        password = (
            cfg.get("CALDAV_PASSWORD")
            or cfg.get("caldav_password")
            or os.getenv("CALDAV_PASSWORD")
            or ""
        ).strip()
        return caldav_url, username, password

    def sync_events(
        self,
        events: Sequence[CalendarEvent],
        *,
        idempotency_prefix: str = "hermes-sync",
        raise_if_unconfigured: bool = False,
    ) -> Dict[str, Any]:
        """Sync events to CalDAV endpoint or report unconfigured.

        Never fakes in-memory success when unconfigured.
        """
        if not self.is_configured():
            if raise_if_unconfigured:
                raise CalendarProviderUnconfiguredError(
                    sanitize_calendar_text("CalDAV / iCloud credentials not configured. " + self._status.actionable_message)
                )
            return {
                "provider": self.name,
                "configured": False,
                "synced_count": 0,
                "events": [],
                "status": "unconfigured",
                "error": "CalDAV / iCloud credentials not configured",
                "actionable_message": self._status.actionable_message,
            }

        import urllib.request
        import base64

        caldav_url, username, password = self._resolve_caldav_config()
        synced_records = []

        for ev in events:
            idempotency_key = f"{idempotency_prefix}:{ev.uid}"
            vevent_ics = export_icalendar([ev])
            event_url = f"{caldav_url.rstrip('/')}/{ev.uid}.ics"
            headers = {
                "Content-Type": "text/calendar; charset=utf-8",
                "If-None-Match": "*",
            }
            if username or password:
                auth_bytes = f"{username}:{password}".encode("utf-8")
                headers["Authorization"] = f"Basic {base64.b64encode(auth_bytes).decode('ascii')}"

            req = urllib.request.Request(
                event_url,
                data=vevent_ics.encode("utf-8"),
                headers=headers,
                method="PUT",
            )
            try:
                with urllib.request.urlopen(req, timeout=10) as resp:
                    http_code = getattr(resp, "status", 200)
                    if http_code not in (200, 201, 204):
                        # Verify remote event ID via GET if confirmation status is unexpected
                        get_headers = {}
                        if username or password:
                            get_headers["Authorization"] = headers["Authorization"]
                        get_req = urllib.request.Request(
                            event_url,
                            headers=get_headers,
                            method="GET",
                        )
                        with urllib.request.urlopen(get_req, timeout=10) as get_resp:
                            ics_text = get_resp.read().decode("utf-8")
                            if ev.uid not in ics_text:
                                raise CalendarProviderError(f"Remote event ID verification failed for {ev.uid}")
                            http_code = getattr(get_resp, "status", 200)

                    synced_records.append({
                        "uid": ev.uid,
                        "idempotency_key": idempotency_key,
                        "status": "confirmed",
                        "http_code": http_code,
                    })
            except urllib.error.HTTPError as exc:
                if exc.code in (409, 412):
                    # HTTP 409 Conflict / 412 Precondition Failed: verify existing event via GET
                    get_headers = {}
                    if username or password:
                        get_headers["Authorization"] = headers["Authorization"]
                    get_req = urllib.request.Request(
                        event_url,
                        headers=get_headers,
                        method="GET",
                    )
                    try:
                        with urllib.request.urlopen(get_req, timeout=10) as get_resp:
                            ics_text = get_resp.read().decode("utf-8")
                            if ev.uid in ics_text:
                                synced_records.append({
                                    "uid": ev.uid,
                                    "idempotency_key": idempotency_key,
                                    "status": "confirmed",
                                    "http_code": getattr(get_resp, "status", 200),
                                })
                                continue
                    except Exception:
                        pass

                sanitized_err = sanitize_calendar_text(str(exc))
                for s in (password, username):
                    if s and s in sanitized_err:
                        sanitized_err = sanitized_err.replace(s, "[REDACTED_SECRET]")
                return {
                    "provider": self.name,
                    "configured": True,
                    "synced_count": len(synced_records),
                    "events": synced_records,
                    "status": "error",
                    "error": sanitized_err,
                }
            except Exception as exc:
                sanitized_err = sanitize_calendar_text(str(exc))
                for s in (password, username):
                    if s and s in sanitized_err:
                        sanitized_err = sanitized_err.replace(s, "[REDACTED_SECRET]")
                return {
                    "provider": self.name,
                    "configured": True,
                    "synced_count": len(synced_records),
                    "events": synced_records,
                    "status": "error",
                    "error": sanitized_err,
                }

        return {
            "provider": self.name,
            "configured": True,
            "synced_count": len(synced_records),
            "events": synced_records,
            "status": "success",
        }

    def read_events(
        self,
        time_min: Optional[datetime] = None,
        time_max: Optional[datetime] = None,
    ) -> List[Dict[str, Any]]:
        if not self.is_configured():
            raise CalendarProviderUnconfiguredError(
                sanitize_calendar_text("CalDAV / iCloud credentials not configured. " + self._status.actionable_message)
            )
        import urllib.request
        import base64

        caldav_url, username, password = self._resolve_caldav_config()

        t_start = (time_min or datetime.now(timezone.utc)).strftime("%Y%m%dT%H%M%SZ")
        t_end = (time_max or (datetime.now(timezone.utc) + timedelta(days=30))).strftime("%Y%m%dT%H%M%SZ")
        query_xml = (
            '<?xml version="1.0" encoding="utf-8" ?>\n'
            '<C:calendar-query xmlns:D="DAV:" xmlns:C="urn:ietf:params:xml:ns:caldav">\n'
            '  <D:prop>\n'
            '    <D:getetag/>\n'
            '    <C:calendar-data/>\n'
            '  </D:prop>\n'
            '  <C:filter>\n'
            '    <C:comp-filter name="VCALENDAR">\n'
            '      <C:comp-filter name="VEVENT">\n'
            f'        <C:time-range start="{t_start}" end="{t_end}"/>\n'
            '      </C:comp-filter>\n'
            '    </C:comp-filter>\n'
            '  </C:filter>\n'
            '</C:calendar-query>'
        )
        headers = {
            "Content-Type": "application/xml; charset=utf-8",
            "Depth": "1",
        }
        if username or password:
            auth_bytes = f"{username}:{password}".encode("utf-8")
            headers["Authorization"] = f"Basic {base64.b64encode(auth_bytes).decode('ascii')}"
        req = urllib.request.Request(
            caldav_url,
            data=query_xml.encode("utf-8"),
            headers=headers,
            method="REPORT",
        )
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                resp_text = resp.read().decode("utf-8")
                events: List[Dict[str, Any]] = []
                for block in resp_text.split("BEGIN:VEVENT"):
                    if "END:VEVENT" in block:
                        vevent = block.split("END:VEVENT")[0]
                        ev_dict: Dict[str, Any] = {}
                        for line in vevent.splitlines():
                            if ":" in line:
                                k, v = line.split(":", 1)
                                if k == "UID":
                                    ev_dict["uid"] = v.strip()
                                elif k == "SUMMARY":
                                    ev_dict["summary"] = v.strip()
                                elif k.startswith("DTSTART"):
                                    ev_dict["start"] = v.strip()
                                elif k.startswith("DTEND"):
                                    ev_dict["end"] = v.strip()
                        if ev_dict:
                            events.append(ev_dict)
                return events
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                return []
            sanitized_err = sanitize_calendar_text(str(exc))
            for s in (password, username):
                if s and s in sanitized_err:
                    sanitized_err = sanitized_err.replace(s, "[REDACTED_SECRET]")
            raise CalendarProviderError(f"CalDAV read failed ({exc.code}): {sanitized_err}") from exc
        except Exception as exc:
            sanitized_err = sanitize_calendar_text(str(exc))
            for s in (password, username):
                if s and s in sanitized_err:
                    sanitized_err = sanitized_err.replace(s, "[REDACTED_SECRET]")
            raise CalendarProviderError(f"CalDAV read failed: {sanitized_err}") from exc

CalDavProvider = CalDAVCalendarProvider
CalDAVProvider = CalDAVCalendarProvider


class CalendarSyncManager:
    """Coordinates syncing across providers with fallback to RFC 5545 .ics export."""

    def __init__(
        self,
        *,
        config: Optional[Dict[str, Any]] = None,
        hermes_dir: Optional[Path] = None,
        google_provider: Optional[GoogleCalendarProvider] = None,
        caldav_provider: Optional[CalDAVCalendarProvider] = None,
    ) -> None:
        self.status = get_calendar_provider_status(config=config, hermes_dir=hermes_dir)
        self.google = google_provider or GoogleCalendarProvider(status=self.status, config=config)
        self.caldav = caldav_provider or CalDAVCalendarProvider(status=self.status, config=config)
    def sync(
        self,
        events: Sequence[CalendarEvent],
        *,
        preferred_provider: Optional[str] = None,
        ics_output_path: Optional[Path | str] = None,
    ) -> Dict[str, Any]:
        """Sync events to preferred/available provider, maintaining valid .ics export."""
        res: Dict[str, Any] = {
            "status_check": self.status.to_dict(),
            "sync_result": None,
            "ics_path": None,
        }

        # Always maintain valid RFC 5545 .ics export as the true file artifact
        if ics_output_path:
            p = write_icalendar(events, ics_output_path)
            res["ics_path"] = str(p)

        target_provider: Optional[CalendarProvider] = None
        if preferred_provider == "google":
            target_provider = self.google
        elif preferred_provider == "caldav":
            target_provider = self.caldav
        elif self.google.is_configured():
            target_provider = self.google
        elif self.caldav.is_configured():
            target_provider = self.caldav

        if target_provider and target_provider.is_configured():
            sync_res = target_provider.sync_events(events)
            res["sync_result"] = sync_res
            res["status"] = "OK" if sync_res.get("status") == "success" else "SYNC_ERROR"
        else:
            res["sync_result"] = {
                "configured": False,
                "status": "unconfigured",
                "message": self.status.actionable_message,
            }
            res["status"] = "FALLBACK_ICS" if ics_output_path else "UNCONFIGURED"

        return res