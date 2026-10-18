"""Plain Markdown notes: one .md file per note in the user's workspace notes folder."""

from __future__ import annotations

from pathlib import Path
import re

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from deeptutor.services.path_service import get_path_service

router = APIRouter()

_NAME = re.compile(r"^[^/\\\0]{1,120}$")


def _dir() -> Path:
    d = get_path_service().get_workspace_feature_dir("notebook") / "notes"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _file(name: str) -> Path:
    if not _NAME.match(name) or name.startswith("."):
        raise HTTPException(400, "Invalid note name")
    return _dir() / (name if name.endswith(".md") else f"{name}.md")


class NoteBody(BaseModel):
    content: str = ""


class RenameBody(BaseModel):
    new_name: str


@router.get("")
def list_notes():
    files = sorted(_dir().glob("*.md"), key=lambda p: p.stat().st_mtime, reverse=True)
    return {"notes": [{"name": p.stem, "updated_at": p.stat().st_mtime} for p in files]}


@router.get("/{name}")
def read_note(name: str):
    p = _file(name)
    if not p.is_file():
        raise HTTPException(404, "Note not found")
    return {"name": p.stem, "content": p.read_text(encoding="utf-8")}


@router.put("/{name}")
def write_note(name: str, body: NoteBody):
    p = _file(name)
    p.write_text(body.content, encoding="utf-8")
    return {"name": p.stem}


@router.post("/{name}/rename")
def rename_note(name: str, body: RenameBody):
    src, dst = _file(name), _file(body.new_name)
    if not src.is_file():
        raise HTTPException(404, "Note not found")
    if dst.exists():
        raise HTTPException(409, "A note with that name exists")
    src.rename(dst)
    return {"name": dst.stem}


@router.delete("/{name}")
def delete_note(name: str):
    p = _file(name)
    if p.is_file():
        trash = _dir() / ".trash"
        trash.mkdir(exist_ok=True)
        p.rename(trash / p.name)
    return {"ok": True}
