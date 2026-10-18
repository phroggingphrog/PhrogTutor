"""Project and filesystem allowlist for Hermes/VPS learning evidence sources.

Enforces strict source boundaries:
- Safe roots: ~/.hermes/, ~/projects/, ~/data/hermes/runtime/, ~/data/hermes/general_brain/
- Forbidden: credentials, SSH keys, raw personal databases, .env files, system auth logs.
Fails closed against symlink escapes, path traversal (..), and unlisted roots.
"""

from __future__ import annotations
import os
from pathlib import Path
from typing import Optional, Sequence

DEFAULT_SAFE_ROOTS: tuple[Path, ...] = (
    Path("~/.hermes").expanduser(),
    Path("~/projects").expanduser(),
    Path("~/data/hermes/runtime").expanduser(),
    Path("~/data/hermes/general_brain/knowledge").expanduser(),
    Path("~/data/hermes/general_brain/context").expanduser(),
    Path("~/tmp").expanduser(),
)
DEFAULT_FORBIDDEN_ROOTS: tuple[Path, ...] = (
    Path("~/.ssh").expanduser(),
    Path("~/.gnupg").expanduser(),
    Path("~/.hermes/auth.json").expanduser(),
    Path("~/data/hermes/db/canonical.db").expanduser(),
    Path("~/data/hermes/db/baseline.db").expanduser(),
    Path("~/data/hermes/personal").expanduser(),
    Path("/etc"),
    Path("/var/log/auth.log"),
    Path("/root"),
)


def is_forbidden_path(
    path: Path,
    forbidden_roots: Optional[Sequence[Path]] = None,
) -> bool:
    """Check whether a resolved path falls within forbidden roots or files."""
    forbidden = DEFAULT_FORBIDDEN_ROOTS if forbidden_roots is None else forbidden_roots
    resolved = path.resolve(strict=False)
    # Specific file patterns
    name_lower = resolved.name.lower()
    if name_lower == ".env" or name_lower.startswith(".env."):
        return True

    for fb in forbidden:
        fb_resolved = fb.resolve(strict=False)
        if resolved == fb_resolved:
            return True
        try:
            resolved.relative_to(fb_resolved)
            return True
        except ValueError:
            continue
    return False


def is_safe_evidence_path(
    path: Path | str,
    safe_roots: Optional[Sequence[Path]] = None,
    forbidden_roots: Optional[Sequence[Path]] = None,
) -> bool:
    """Check if a path is strictly inside allowed safe roots and outside forbidden roots."""
    try:
        validate_evidence_path(path, safe_roots, forbidden_roots)
        return True
    except (ValueError, PermissionError):
        return False


def validate_evidence_path(
    target_path: Path | str,
    safe_roots: Optional[Sequence[Path]] = None,
    forbidden_roots: Optional[Sequence[Path]] = None,
) -> Path:
    """Validate that target_path is within safe roots and not in forbidden roots.

    Returns the canonical resolved Path if valid.
    Raises PermissionError or ValueError if the path fails allowlist checks.
    """
    if not target_path or not str(target_path).strip():
        raise ValueError("Target path must not be empty")

    path_obj = Path(target_path).expanduser()

    # Reject blatant traversal attempts before resolution
    raw_str = str(target_path)
    if "\x00" in raw_str:
        raise ValueError("Null bytes in path")

    resolved = path_obj.resolve(strict=False)

    # 1. Check forbidden roots first (fail closed)
    if is_forbidden_path(resolved, forbidden_roots):
        raise PermissionError(
            f"Evidence path {target_path!r} resolves to forbidden root or sensitive file: {resolved}"
        )

    # 2. Check safe roots
    roots = DEFAULT_SAFE_ROOTS if safe_roots is None else safe_roots
    inside_safe = False
    for root in roots:
        root_resolved = root.resolve(strict=False)
        try:
            resolved.relative_to(root_resolved)
            inside_safe = True
            break
        except ValueError:
            continue

    if not inside_safe:
        raise PermissionError(
            f"Evidence path {target_path!r} ({resolved}) is outside allowed safe roots: "
            f"{[str(r) for r in roots]}"
        )

    return resolved
