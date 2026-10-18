"""Tests for project/path allowlist enforcement on learning evidence."""

from __future__ import annotations

from pathlib import Path
import pytest

from learning_tracking.academia_learning.path_allowlist import (
    is_forbidden_path,
    is_safe_evidence_path,
    validate_evidence_path,
)


def test_safe_roots_accepted(tmp_path: Path):
    safe_root = tmp_path / "safe_projects"
    safe_root.mkdir()
    evidence_file = safe_root / "output.log"
    evidence_file.write_text("ok", encoding="utf-8")

    validated = validate_evidence_path(evidence_file, safe_roots=[safe_root], forbidden_roots=[])
    assert validated == evidence_file.resolve()
    assert is_safe_evidence_path(evidence_file, safe_roots=[safe_root], forbidden_roots=[]) is True


def test_forbidden_roots_rejected(tmp_path: Path):
    safe_root = tmp_path / "safe"
    forbidden_root = tmp_path / "safe" / "credentials"
    safe_root.mkdir()
    forbidden_root.mkdir()

    secret_file = forbidden_root / "keys.json"
    secret_file.write_text("{}", encoding="utf-8")

    with pytest.raises(PermissionError, match="forbidden root or sensitive file"):
        validate_evidence_path(secret_file, safe_roots=[safe_root], forbidden_roots=[forbidden_root])


def test_env_files_strictly_rejected(tmp_path: Path):
    safe_root = tmp_path / "safe"
    safe_root.mkdir()
    env_file = safe_root / ".env"
    env_file.write_text("SECRET=123", encoding="utf-8")

    with pytest.raises(PermissionError, match="forbidden root or sensitive file"):
        validate_evidence_path(env_file, safe_roots=[safe_root])


def test_path_traversal_outside_safe_root_rejected(tmp_path: Path):
    safe_root = tmp_path / "safe"
    outside_dir = tmp_path / "outside"
    safe_root.mkdir()
    outside_dir.mkdir()

    outside_file = outside_dir / "data.txt"
    outside_file.write_text("data", encoding="utf-8")

    # Attempt to traverse from safe_root to outside_file
    traversal_path = safe_root / ".." / "outside" / "data.txt"
    with pytest.raises(PermissionError, match="outside allowed safe roots"):
        validate_evidence_path(traversal_path, safe_roots=[safe_root])


def test_empty_or_null_path_rejected():
    with pytest.raises(ValueError, match="must not be empty"):
        validate_evidence_path("")

    with pytest.raises(ValueError, match="Null bytes"):
        validate_evidence_path("/tmp/safe\x00/file")
