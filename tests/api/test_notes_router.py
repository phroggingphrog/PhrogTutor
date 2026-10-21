"""Notes router: backlinks, link-preserving rename, and trash that never overwrites."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from deeptutor.api.routers import notes
from deeptutor.services.notes_links import parse_links, resolve_link, rewrite_links


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(notes, "_dir", lambda: tmp_path)
    monkeypatch.delenv("PI_TUTOR_NOTE_SOURCES", raising=False)
    app = FastAPI()
    app.include_router(notes.router, prefix="/api/notes")
    return TestClient(app)


def put(client, name, content):
    assert client.put(f"/api/notes/{name}", json={"content": content}).status_code == 200


def read(client, name):
    return client.get(f"/api/notes/{name}").json()["content"]


def test_resolve_link_rules() -> None:
    names = ["Physics/Waves", "Waves", "@wiki/Waves", "Math/Fourier"]
    assert resolve_link("Waves", names) == "Waves"
    assert resolve_link("physics/waves.md", names) == "Physics/Waves"
    assert resolve_link("fourier", names) == "Math/Fourier"
    assert resolve_link("Nope", names) is None
    assert resolve_link("@wiki/Waves", names) == "@wiki/Waves"
    assert resolve_link("Waves", ["@wiki/Waves", "a/b/Waves"]) == "a/b/Waves"


def test_parse_links_skips_code_and_keeps_alias_out() -> None:
    text = "See [[A|alias]] and [[B#head]].\n```\n[[Code]]\n```\n`[[Inline]]`"
    assert parse_links(text) == ["A", "B"]


def test_rewrite_keeps_alias_and_heading() -> None:
    names = ["A", "B"]
    text = "[[A]] [[A|x]] [[A#h]] [[B]]\n```\n[[A]]\n```"
    out = rewrite_links(text, "A", "Folder/C", names)
    assert out == "[[C]] [[C|x]] [[C#h]] [[B]]\n```\n[[A]]\n```"
    assert rewrite_links("[[A]]", "A", "Folder/C", ["A", "Other/C"]) == "[[Folder/C]]"


def test_backlinks(client) -> None:
    put(client, "Physics/Fourier", "See [[Waves]] and [[Missing]].")
    put(client, "Physics/Waves", "Back to [[Fourier]].")
    put(client, "Other", "no links")
    hits = client.get("/api/notes/_backlinks", params={"name": "Physics/Waves"}).json()["backlinks"]
    assert [h["name"] for h in hits] == ["Physics/Fourier"]
    assert "[[Waves]]" in hits[0]["snippet"]


def test_rename_rewrites_links_everywhere(client) -> None:
    put(client, "Fourier", "See [[Waves]] and [[Waves|the waves]].")
    put(client, "Waves", "Self [[Waves]]")
    r = client.post("/api/notes/Waves/rename", json={"new_name": "Physics/Waves"})
    assert r.status_code == 200 and r.json()["name"] == "Physics/Waves"
    assert read(client, "Fourier") == "See [[Waves]] and [[Waves|the waves]]."
    assert read(client, "Physics/Waves") == "Self [[Waves]]"
    r = client.post("/api/notes/Physics/Waves/rename", json={"new_name": "Physics/Wave motion"})
    assert read(client, "Fourier") == "See [[Wave motion]] and [[Wave motion|the waves]]."


def test_rename_onto_existing_is_refused(client) -> None:
    put(client, "A", "x")
    put(client, "B", "y")
    assert client.post("/api/notes/A/rename", json={"new_name": "B"}).status_code == 409
    assert read(client, "B") == "y"


def test_trash_keeps_both_same_named_notes(client, tmp_path) -> None:
    put(client, "a/Notes", "first")
    put(client, "b/Notes", "second")
    client.delete("/api/notes/a/Notes")
    client.delete("/api/notes/b/Notes")
    kept = sorted(p.read_text() for p in (tmp_path / ".trash").iterdir())
    assert kept == ["first", "second"]


PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 32


def test_image_upload_is_stored_by_content_and_served(client, tmp_path) -> None:
    r = client.post("/api/notes/_assets", files={"file": ("shot.png", PNG, "image/png")})
    assert r.status_code == 200
    path = r.json()["path"]
    assert path.startswith("_assets/") and path.endswith(".png")
    again = client.post("/api/notes/_assets", files={"file": ("other.png", PNG, "image/png")})
    assert again.json()["path"] == path  # same bytes, same file
    got = client.get(f"/api/notes/{path}")
    assert got.status_code == 200 and got.content == PNG
    assert got.headers["content-type"] == "image/png" and got.headers["x-content-type-options"] == "nosniff"


def test_image_upload_refuses_svg_html_and_oversize(client, monkeypatch) -> None:
    svg = b"<svg xmlns='http://www.w3.org/2000/svg'><script>alert(1)</script></svg>"
    assert client.post("/api/notes/_assets", files={"file": ("a.png", svg, "image/png")}).status_code == 415
    assert client.post("/api/notes/_assets", files={"file": ("a.png", b"<html>", "image/png")}).status_code == 415
    monkeypatch.setattr(notes, "_MAX_IMAGE_BYTES", 16)
    assert client.post("/api/notes/_assets", files={"file": ("a.png", PNG, "image/png")}).status_code == 413


def test_asset_route_refuses_traversal_and_unknown_names(client) -> None:
    assert client.get("/api/notes/_assets/..%2Fsecret.png").status_code in (400, 404)
    assert client.get("/api/notes/_assets/abcdef0123456789.png").status_code == 404
    assert client.get("/api/notes/_assets/notes.md").status_code == 404
