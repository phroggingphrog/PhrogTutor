"""A connected wiki KB searches its own qmd URL and collections, and is never indexed."""

from __future__ import annotations

import pytest

from deeptutor.knowledge.kb_types import WIKI_KB_TYPE, is_connected_kb
from deeptutor.knowledge.manager import KnowledgeBaseManager
from deeptutor.services.rag.pipelines.wiki.pipeline import WikiQmdPipeline
from deeptutor.services.rag.provider_binding import resolve_bound_provider


class _FakeClient:
    def __init__(self) -> None:
        self.queries: list[dict] = []

    async def query(self, query, *, collections=None, limit=5, intent=None):
        self.queries.append({"query": query, "collections": collections})
        return {"results": [{"file": "university/a.md", "title": "A", "score": 0.9}]}

    async def get(self, file, *, max_lines=None):
        return "note body"


def test_register_wiki_kb_is_a_connected_pointer(tmp_path) -> None:
    manager = KnowledgeBaseManager(base_dir=str(tmp_path))
    entry = manager.register_wiki_kb("my-wiki", "http://127.0.0.1:8906/mcp", ["university", " "])
    assert entry["type"] == WIKI_KB_TYPE
    assert entry["collections"] == ["university"]
    assert is_connected_kb(entry)
    assert resolve_bound_provider(tmp_path, "my-wiki") == "wiki"
    with pytest.raises(ValueError):
        manager.register_wiki_kb("my-wiki", "http://127.0.0.1:8906/mcp")


@pytest.mark.asyncio
async def test_search_uses_the_kb_collections(tmp_path) -> None:
    manager = KnowledgeBaseManager(base_dir=str(tmp_path))
    manager.register_wiki_kb("my-wiki", "http://127.0.0.1:8906/mcp", ["personal-learning"])
    client = _FakeClient()
    pipeline = WikiQmdPipeline(str(tmp_path), client_factory=lambda: client)
    result = await pipeline.search("fourier", "my-wiki")
    assert client.queries[0]["collections"] == ["personal-learning"]
    assert result["notes"][0]["content"] == "note body"
    assert result["collections"] == ["personal-learning"]


@pytest.mark.asyncio
async def test_wiki_never_indexes(tmp_path) -> None:
    pipeline = WikiQmdPipeline(str(tmp_path))
    with pytest.raises(RuntimeError):
        await pipeline.add_documents("my-wiki", ["x.md"])


@pytest.mark.asyncio
async def test_wiki_get_reads_through_the_kb_pointer(tmp_path, monkeypatch) -> None:
    from deeptutor.services.rag.pipelines.wiki import binding
    from deeptutor.services.rag.pipelines.wiki import client as client_module
    from deeptutor.tools.builtin import WikiGetTool

    manager = KnowledgeBaseManager(base_dir=str(tmp_path))
    manager.register_wiki_kb("my-wiki", "http://127.0.0.1:8906/mcp")
    entry = {"type": "wiki", "server_url": "http://127.0.0.1:8906/mcp"}
    monkeypatch.setattr(binding, "wiki_kb_entry", lambda name: entry if name == "my-wiki" else None)

    class _Client(_FakeClient):
        def __init__(self, url):
            super().__init__()

    monkeypatch.setattr(client_module, "WikiQmdClient", _Client)
    tool = WikiGetTool()
    ok = await tool.execute(kb_name="my-wiki", file="university/a.md")
    assert ok.content == "note body" and ok.success
    other = await tool.execute(kb_name="plain-kb", file="x.md")
    assert not other.success


def test_wiki_get_mounts_only_with_a_wiki_kb() -> None:
    from deeptutor.agents._shared.tool_composition import ToolMountFlags, compose_enabled_tools

    class _Registry:
        def get_enabled(self, names):
            return []

    def mounted(**flags):
        return compose_enabled_tools(
            registry=_Registry(),
            requested_tools=[],
            optional_whitelist=[],
            mount_flags=ToolMountFlags(**flags),
        )

    assert "wiki_get" in mounted(has_kb=True, has_wiki=True)
    assert "wiki_get" not in mounted(has_kb=True)
