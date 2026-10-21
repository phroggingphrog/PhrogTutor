"""Plain Markdown notes: one .md file per note, in folders.

The user's own notes live in the workspace notes folder (read-write). Extra folders, such as an
Obsidian vault or an LLM wiki, can be opened read-only with PI_TUTOR_NOTE_SOURCES, e.g.
``PI_TUTOR_NOTE_SOURCES="wiki=/home/me/brain/wiki;vault=/home/me/Obsidian"``. Their notes are named
``@wiki/folder/note``.
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
import re
import time

from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel

from deeptutor.services.notes_index import NotesIndex, index_for
from deeptutor.services.notes_links import rewrite_links
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


def _all_notes() -> list[tuple[str, Path, bool]]:
    """(name, file, read_only) for every note, own notes first, capped at ``_MAX_LIST``."""
    found: list[tuple[str, Path, bool]] = []
    roots = [(None, _dir().resolve(), False), *[(k, v, True) for k, v in _sources().items()]]
    for label, root, read_only in roots:
        for p in root.rglob("*.md"):
            rel = p.relative_to(root)
            if any(part.startswith(".") for part in rel.parts):
                continue
            found.append((_name(root, p, label), p, read_only))
            if len(found) >= _MAX_LIST:
                return found
    return found


def _index() -> NotesIndex:
    index = index_for(_dir().resolve())
    index.sync(_all_notes())
    return index


@router.get("")
def list_notes():
    notes = [
        {"name": name, "updated_at": p.stat().st_mtime, "read_only": read_only}
        for name, p, read_only in _all_notes()
    ]
    notes.sort(key=lambda n: (n["read_only"], -n["updated_at"]))
    return {"notes": notes, "sources": sorted(_sources())}


_MAX_IMAGE_BYTES = 10 * 1024 * 1024
_ASSET_NAME = re.compile(r"^[0-9a-f]{16}\.(png|jpg|gif|webp)$")
_MEDIA = {"png": "image/png", "jpg": "image/jpeg", "gif": "image/gif", "webp": "image/webp"}


def _image_extension(data: bytes) -> str | None:
    """The image type from its magic bytes. The client's content type is not trusted, and SVG is refused."""
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if data.startswith(b"\xff\xd8\xff"):
        return "jpg"
    if data.startswith((b"GIF87a", b"GIF89a")):
        return "gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    return None


@router.post("/_assets")
async def upload_asset(file: UploadFile = File(...)):
    """Store a pasted or dropped image; the note refers to it as ``_assets/<name>``."""
    data = await file.read(_MAX_IMAGE_BYTES + 1)
    if len(data) > _MAX_IMAGE_BYTES:
        raise HTTPException(413, "Image is larger than 10 MB")
    ext = _image_extension(data)
    if ext is None:
        raise HTTPException(415, "Only PNG, JPEG, GIF and WebP images can be added")
    name = f"{hashlib.sha256(data).hexdigest()[:16]}.{ext}"
    folder = _dir() / "_assets"
    folder.mkdir(exist_ok=True)
    target = folder / name
    if not target.exists():
        target.write_bytes(data)
    return {"path": f"_assets/{name}"}


@router.get("/_assets/{filename}")
def read_asset(filename: str):
    if not _ASSET_NAME.match(filename):
        raise HTTPException(404, "Image not found")
    path = _dir() / "_assets" / filename
    if not path.is_file():
        raise HTTPException(404, "Image not found")
    return FileResponse(
        path,
        media_type=_MEDIA[filename.rsplit(".", 1)[1]],
        headers={"X-Content-Type-Options": "nosniff", "Cache-Control": "private, max-age=31536000, immutable"},
    )


@router.get("/_backlinks")
def backlinks(name: str):
    """Notes that link to ``name`` with ``[[wikilinks]]``, each with a short snippet."""
    _locate(name)
    return {"backlinks": _index().backlinks(name)}


@router.get("/_tags")
def tags():
    """Every ``#tag`` used in the notes, most used first."""
    return {"tags": _index().tags()}


@router.get("/_graph")
def graph():
    """Notes as nodes and resolved wikilinks as edges."""
    return _index().graph()


@router.get("/search")
def search_notes(q: str = "", tag: str = ""):
    """Notes whose text contains every word of q (best matches first), optionally only those with a tag."""
    return {"hits": _index().search(q, tag)}


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
    if dst.exists() and dst != src:
        raise HTTPException(409, "A note with that name exists")
    notes = _all_notes()
    names = [n for n, _, _ in notes]
    new_name = body.new_name.strip("/")
    dst.parent.mkdir(parents=True, exist_ok=True)
    src.rename(dst)
    # Keep links working: rewrite [[links]] that pointed at the old name, in notes the user can edit.
    for other, p, read_only in notes:
        if read_only:
            continue
        if other == name:
            p = dst
        text = p.read_text(encoding="utf-8", errors="replace")
        if "[[" not in text:
            continue
        updated = rewrite_links(text, name, new_name, names)
        if updated != text:
            p.write_text(updated, encoding="utf-8")
    return {"name": new_name}


@router.delete("/{name:path}")
def delete_note(name: str):
    path = _writable(name)
    if path.is_file():
        trash = _dir() / ".trash"
        trash.mkdir(exist_ok=True)
        # Same file name in two folders must not overwrite an earlier trashed note.
        path.rename(trash / f"{int(time.time() * 1000)}-{path.name}")
    return {"ok": True}
