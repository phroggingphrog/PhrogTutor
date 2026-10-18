"""Plain Markdown notes: one .md file per note, in folders.

The user's own notes live in the workspace notes folder (read-write). Extra folders, such as an
Obsidian vault or an LLM wiki, can be opened read-only with PI_TUTOR_NOTE_SOURCES, e.g.
``PI_TUTOR_NOTE_SOURCES="wiki=/home/me/brain/wiki;vault=/home/me/Obsidian"``. Their notes are named
``@wiki/folder/note``.
"""

from __future__ import annotations

import os
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from deeptutor.services.path_service import get_path_service

router = APIRouter()

_MAX_LIST = 20_000


def _dir() -> Path:
    d = get_path_service().get_workspace_feature_dir("notebook") / "notes"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _sources() -> dict[str, Path]:
    out = {}
    for item in os.environ.get("PI_TUTOR_NOTE_SOURCES", "").split(";"):
        label, _, path = item.partition("=")
        if label.strip() and path.strip() and Path(path.strip()).expanduser().is_dir():
            out[label.strip()] = Path(path.strip()).expanduser().resolve()
    return out


def _locate(name: str) -> tuple[Path, Path, bool]:
    """(root, file, read_only) for a note name; refuses anything that escapes its root."""
    if name.startswith("@"):
        label, _, rel = name[1:].partition("/")
        root = _sources().get(label)
        if root is None:
            raise HTTPException(404, "Unknown note source")
        read_only = True
    else:
        root, rel, read_only = _dir().resolve(), name, False
    rel = rel.strip("/")
    if not rel or any(part.startswith(".") for part in rel.split("/")) or "\0" in rel or len(rel) > 300:
        raise HTTPException(400, "Invalid note name")
    path = (root / (rel if rel.endswith(".md") else f"{rel}.md")).resolve()
    if root not in path.parents:
        raise HTTPException(400, "Invalid note name")
    return root, path, read_only


def _name(root: Path, path: Path, label: str | None) -> str:
    rel = path.relative_to(root).with_suffix("").as_posix()
    return f"@{label}/{rel}" if label else rel


def _writable(name: str) -> Path:
    _, path, read_only = _locate(name)
    if read_only:
        raise HTTPException(403, "This folder is read-only")
    return path


class NoteBody(BaseModel):
    content: str = ""


class RenameBody(BaseModel):
    new_name: str


@router.get("")
def list_notes():
    notes = []
    roots = [(None, _dir().resolve(), False), *[(k, v, True) for k, v in _sources().items()]]
    for label, root, read_only in roots:
        for p in root.rglob("*.md"):
            rel = p.relative_to(root)
            if any(part.startswith(".") for part in rel.parts):
                continue
            notes.append({"name": _name(root, p, label), "updated_at": p.stat().st_mtime, "read_only": read_only})
            if len(notes) >= _MAX_LIST:
                break
    notes.sort(key=lambda n: (n["read_only"], -n["updated_at"]))
    return {"notes": notes, "sources": sorted(_sources())}


@router.get("/search")
def search_notes(q: str = ""):
    """Notes whose text contains every word of q (case-insensitive), with a short snippet."""
    words = [w for w in q.lower().split() if w]
    hits = []
    if not words:
        return {"hits": hits}
    roots = [(None, _dir().resolve()), *_sources().items()]
    for label, root in roots:
        for p in root.rglob("*.md"):
            if any(part.startswith(".") for part in p.relative_to(root).parts):
                continue
            try:
                text = p.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            low = text.lower()
            if all(w in low for w in words):
                at = low.find(words[0])
                snippet = " ".join(text[max(0, at - 60): at + 100].split())
                hits.append({"name": _name(root, p, label), "snippet": snippet})
                if len(hits) >= 50:
                    return {"hits": hits}
    return {"hits": hits}


@router.get("/{name:path}")
def read_note(name: str):
    root, path, read_only = _locate(name)
    if not path.is_file():
        raise HTTPException(404, "Note not found")
    return {"name": name, "content": path.read_text(encoding="utf-8", errors="replace"), "read_only": read_only}


@router.put("/{name:path}")
def write_note(name: str, body: NoteBody):
    path = _writable(name)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body.content, encoding="utf-8")
    return {"name": name}


@router.post("/{name:path}/rename")
def rename_note(name: str, body: RenameBody):
    src, dst = _writable(name), _writable(body.new_name)
    if not src.is_file():
        raise HTTPException(404, "Note not found")
    if dst.exists():
        raise HTTPException(409, "A note with that name exists")
    dst.parent.mkdir(parents=True, exist_ok=True)
    src.rename(dst)
    return {"name": body.new_name.strip("/")}


@router.delete("/{name:path}")
def delete_note(name: str):
    path = _writable(name)
    if path.is_file():
        trash = _dir() / ".trash"
        trash.mkdir(exist_ok=True)
        path.rename(trash / path.name)
    return {"ok": True}
