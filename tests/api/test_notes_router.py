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


def test_parse_tags_ignores_headings_code_urls_and_fragments() -> None:
    from deeptutor.services.notes_links import parse_tags

    text = "# Heading\n\nStudy #Physics and #math/calculus, #physics again.\nSee http://x.test/#anchor and page#frag.\n`#code`\n```\n#fenced\n```\n#1 #2024"
    assert parse_tags(text) == ["physics", "math/calculus"]


def test_search_ranks_title_and_tags_above_body_and_filters_by_tag(client) -> None:
    put(client, "Fourier", "The fourier transform. #physics")
    put(client, "Notes on waves", "mentions fourier once")
    put(client, "Unrelated", "nothing here #physics")
    hits = client.get("/api/notes/search", params={"q": "fourier"}).json()["hits"]
    assert [h["name"] for h in hits] == ["Fourier", "Notes on waves"]
    assert "fourier" in hits[0]["snippet"].lower()
    tagged = client.get("/api/notes/search", params={"tag": "#Physics"}).json()["hits"]
    assert sorted(h["name"] for h in tagged) == ["Fourier", "Unrelated"]
    both = client.get("/api/notes/search", params={"q": "fourier", "tag": "physics"}).json()["hits"]
    assert [h["name"] for h in both] == ["Fourier"]
    assert client.get("/api/notes/search").json() == {"hits": []}


def test_tags_endpoint_counts(client) -> None:
    put(client, "A", "#x #y")
    put(client, "B", "#x")
    assert client.get("/api/notes/_tags").json()["tags"] == [{"tag": "x", "count": 2}, {"tag": "y", "count": 1}]


def test_graph_has_resolved_edges_and_keeps_loose_notes(client) -> None:
    put(client, "A", "to [[B]] and [[Nope]]")
    put(client, "B", "back [[A]]")
    put(client, "C", "alone")
    g = client.get("/api/notes/_graph").json()
    assert {n["id"]: n["links"] for n in g["nodes"]} == {"A": 2, "B": 2, "C": 0}
    assert {(e["source"], e["target"]) for e in g["edges"]} == {("A", "B"), ("B", "A")}


def test_index_rereads_only_changed_files(tmp_path, monkeypatch) -> None:
    from deeptutor.services.notes_index import NotesIndex

    a, b = tmp_path / "a.md", tmp_path / "b.md"
    a.write_text("alpha")
    b.write_text("beta")
    rows = [("a", a, False), ("b", b, False)]
    index = NotesIndex()
    index.sync(rows)
    reads: list[str] = []
    real = type(a).read_text

    def spy(self, *args, **kwargs):
        reads.append(self.name)
        return real(self, *args, **kwargs)

    monkeypatch.setattr(type(a), "read_text", spy)
    index.sync(rows)
    assert reads == []
    b.write_text("gamma!")
    index.sync(rows)
    assert reads == ["b.md"]
    assert [h["name"] for h in index.search("gamma")] == ["b"]
    b.unlink()
    index.sync([("a", a, False)])
    assert index.search("gamma") == []
