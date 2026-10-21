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
