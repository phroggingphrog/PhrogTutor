from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple
import urllib.error
import urllib.parse
import urllib.request

from .models import Assignment


def _parse_datetime(value: Any) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))


def _deadline_dates(
    due_at: datetime | None, known_original: datetime | None
) -> tuple[datetime | None, datetime | None]:
    if due_at is None:
        return None, None
    computed_original = due_at - timedelta(days=14)
    personal_final = min(computed_original, known_original) if known_original else computed_original
    return personal_final, personal_final - timedelta(days=7)


def get_canvas_config(
    env_path: Optional[Path | str] = None,
) -> Tuple[str, str]:
    """Retrieve Canvas Base URL and API Token from environment or .env files."""
    token = os.environ.get("CANVAS_API_TOKEN", "")
    base_url = os.environ.get("CANVAS_BASE_URL", "")

    candidate_paths = []
    if env_path:
        candidate_paths.append(Path(env_path))
    hermes_home = os.environ.get("HERMES_HOME", os.path.expanduser("~/.hermes"))
    candidate_paths.extend([
        Path(".env"),
        Path(hermes_home) / ".env",
        Path(os.path.expanduser("~/.env")),
    ])

    for p in candidate_paths:
        if (not token or not base_url) and p.is_file():
            try:
                for line in p.read_text(encoding="utf-8").splitlines():
                    line = line.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    k, v = line.split("=", 1)
                    k, v = k.strip(), v.strip().strip("'\"")
                    if k == "CANVAS_API_TOKEN" and not token:
                        token = v
                    elif k == "CANVAS_BASE_URL" and not base_url:
                        base_url = v
            except Exception:
                pass

    return base_url.rstrip("/"), token


def fetch_assignments_from_api(
    base_url: Optional[str] = None,
    token: Optional[str] = None,
    course_ids: Optional[Sequence[str | int]] = None,
    env_path: Optional[Path | str] = None,
    max_items: int = 200,
    timeout: int = 30,
) -> List[Assignment]:
    """Fetch assignments directly from the Canvas REST API in a read-only manner."""
    cfg_url, cfg_token = get_canvas_config(env_path)
    url = (base_url or cfg_url).rstrip("/")
    api_token = token or cfg_token

    if not url or not api_token:
        raise ValueError(
            "Canvas API configuration missing. Ensure CANVAS_BASE_URL and CANVAS_API_TOKEN are set in .env or environment."
        )

    if not url.startswith("https://"):
        raise ValueError("Canvas base URL must use HTTPS")

    base_split = urllib.parse.urlsplit(url)
    headers = {
        "Authorization": f"Bearer {api_token}",
        "Accept": "application/json",
        "User-Agent": "Hermes-Academia-Learning/1.0",
    }

    def _get_json(endpoint_url: str, params: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
        results: List[Dict[str, Any]] = []
        cur_url: Optional[str] = endpoint_url
        if params:
            cur_url = f"{cur_url}?{urllib.parse.urlencode(params)}"

        while cur_url and len(results) < max_items:
            req = urllib.request.Request(cur_url, headers=headers, method="GET")
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                if isinstance(data, list):
                    results.extend(data)
                elif isinstance(data, dict):
                    results.append(data)

                # Check Link header for pagination
                link_header = resp.headers.get("Link", "")
                cur_url = None
                if link_header:
                    for part in link_header.split(","):
                        if 'rel="next"' in part:
                            match = part.strip().split(";")[0].strip("<>")
                            u = urllib.parse.urlsplit(match)
                            if (u.scheme, u.netloc) != (base_split.scheme, base_split.netloc):
                                raise ValueError("Cross-origin or insecure pagination URL rejected")
                            cur_url = match
                            break
        return results

    # If course_ids not provided, query enrolled courses
    courses_to_query: List[Dict[str, Any]] = []
    if course_ids:
        courses_to_query = [{"id": cid, "name": f"Course-{cid}"} for cid in course_ids]
    else:
        courses_endpoint = f"{url}/api/v1/courses"
        raw_courses = _get_json(courses_endpoint, {"enrollment_state": "active", "per_page": 50})
        courses_to_query = [
            {"id": c.get("id"), "name": c.get("name") or c.get("course_code") or ""}
            for c in raw_courses
            if "id" in c
        ]

    assignments_out: List[Assignment] = []
    for c in courses_to_query:
        cid = c["id"]
        cname = c.get("name", "")
        assign_endpoint = f"{url}/api/v1/courses/{cid}/assignments"
        try:
            raw_assigns = _get_json(assign_endpoint, {"per_page": 50, "order_by": "due_at"})
        except ValueError:
            raise
        except Exception:
            continue

        for item in raw_assigns:
            due_at_str = item.get("due_at")
            if due_at_str:
                due_at = _parse_datetime(due_at_str)
                personal_final, protection_start = _deadline_dates(
                    due_at, _parse_datetime(item.get("original_due_at"))
                )
                original_due_at = personal_final
                extended_due_at = due_at
            else:
                due_at = None
                original_due_at = None
                personal_final = None
                extended_due_at = None
                protection_start = None

            assignments_out.append(
                Assignment(
                    id=str(item.get("id", f"canvas-{len(assignments_out)+1}")),
                    title=item.get("name") or item.get("title") or "Untitled Assignment",
                    course=cname,
                    due_at=due_at,
                    personal_final=personal_final,
                    prerequisites=tuple(item.get("prerequisites", ())),
                    progress=float(item.get("progress", 0.0)),
                    original_due_at=original_due_at,
                    extended_due_at=extended_due_at,
                    protection_start=protection_start,
                )
            )

    return assignments_out


def load_assignments(
    path: Optional[Path | str] = None,
    *,
    fetch_live: bool = False,
    env_path: Optional[Path | str] = None,
) -> list[Assignment]:
    """Load assignments from a local JSON summary or live Canvas API."""
    if fetch_live or path is None or str(path).lower() in ("api", "live", "none"):
        return fetch_assignments_from_api(env_path=env_path)

    p = Path(path)
    if not p.exists() and (fetch_live or str(path).startswith("http")):
        return fetch_assignments_from_api(env_path=env_path)

    data = json.loads(p.read_text(encoding="utf-8"))
    assignments = data["assignments" if "assignments" in data else "upcoming"]
    result = []
    for item in assignments:
        due_at_str = item.get("due_at")
        if due_at_str:
            due_at = _parse_datetime(due_at_str)
            personal_final, protection_start = _deadline_dates(
                due_at, _parse_datetime(item.get("original_due_at"))
            )
            original_due_at = personal_final
            extended_due_at = due_at
        else:
            due_at = None
            original_due_at = None
            personal_final = None
            extended_due_at = None
            protection_start = None
        result.append(
            Assignment(
                id=str(item["id"]),
                title=item["name"],
                course=item.get("course_name", ""),
                due_at=due_at,
                personal_final=personal_final,
                prerequisites=tuple(item.get("prerequisites", ())),
                progress=float(item.get("progress", 0)),
                original_due_at=original_due_at,
                extended_due_at=extended_due_at,
                protection_start=protection_start,
            )
        )
    return result
