"""Retrieval-only RAG pipeline over the local wiki qmd index.

The wiki already holds the owner's university material and learning evidence as
markdown, and qmd already indexes it. This pipeline adds no index, no embedding
and no document copy: it answers the normal RAG pipeline contract by calling the
qmd MCP server that ``pi_engine/turn.ts`` also uses for ``llm_wiki__query``.

Providers is opt-in: ``PI_TUTOR_RAG=wiki`` selects it, otherwise the factory
still builds the default LlamaIndex/faiss pipeline.
"""

from __future__ import annotations

import logging
import os
from typing import Any

from deeptutor.runtime.home import get_runtime_data_root
from deeptutor.services.rag.provider_binding import load_kb_config_entry

from .client import (
    DEFAULT_URL,
    MAX_GET_CHARS,
    PROVIDER,
    WikiQmdClient,
    WikiQmdError,
    resolve_collections,
    result_file,
    result_score,
    result_title,
)

logger = logging.getLogger(__name__)

DEFAULT_TOP_K = 5
# Pages opened after a search, so ``content`` is real note text rather than the
# title list a bare ``query`` returns. Mirrors ``get`` on the search paths.
HYDRATE_LIMIT = 3


def wiki_rag_enabled() -> bool:
    """Whether the wiki qmd pipeline replaces the default retrieval provider."""
    return (os.environ.get("PI_TUTOR_RAG") or "").strip().lower() == PROVIDER


def wiki_url() -> str:
    return (os.environ.get("PI_TUTOR_WIKI_URL") or "").strip() or DEFAULT_URL


class WikiQmdPipeline:
    """Search the owner's wiki through qmd instead of a local vector index."""

    def __init__(
        self,
        kb_base_dir: str | None = None,
        *,
        url: str | None = None,
        client_factory: Any = None,
        **_: Any,
    ) -> None:
        self.kb_base_dir = kb_base_dir or str(get_runtime_data_root() / "knowledge_bases")
        self.url = url or wiki_url()
        self._default_factory = client_factory is None
        self._client_factory = client_factory or (lambda: WikiQmdClient(self.url))
        self.logger = logger

    def _client(self) -> Any:
        return self._client_factory()

    async def search(self, query: str, kb_name: str, **kwargs: Any) -> dict[str, Any]:
        """Retrieve wiki notes for ``query``, in the shared RAG result shape."""
        # A connected wiki KB carries its own URL and collections; the env
        # fallback path (PI_TUTOR_RAG=wiki) has no entry and keeps the old behaviour.
        entry = load_kb_config_entry(self.kb_base_dir, kb_name)
        pointed = entry.get("type") == PROVIDER
        wanted = kwargs.get("collections") or (entry.get("collections") if pointed else None)
        try:
            collections = resolve_collections(wanted)
        except WikiQmdError as exc:
            return self._error(query, str(exc), "not_configured")
        limit = max(1, min(int(kwargs.get("top_k") or DEFAULT_TOP_K), 20))
        try:
            client = WikiQmdClient(entry["server_url"]) if pointed and entry.get("server_url") and self._default_factory else self._client()
            found = await client.query(
                query,
                collections=collections,
                limit=limit,
                intent=kwargs.get("intent"),
            )
        except WikiQmdError as exc:
            return self._error(query, str(exc), "retrieval_error")
        except Exception as exc:  # defensive: a bad client never fails a turn
            self.logger.warning("wiki qmd search failed: %s", exc)
            return self._error(query, "The wiki qmd search failed.", "retrieval_error")

        hits = found.get("results") or []
        if not hits:
            return {
                "query": query,
                "answer": "",
                "content": "",
                "sources": [],
                "provider": PROVIDER,
                "collections": collections,
                "notes": [],
            }

        sources: list[dict[str, Any]] = []
        notes: list[dict[str, Any]] = []
        for index, hit in enumerate(hits):
            if not isinstance(hit, dict):
                continue
            path = result_file(hit)
            sources.append(
                {
                    "title": result_title(hit, path or f"Result {index + 1}"),
                    "content": str(hit.get("context") or "")[:MAX_GET_CHARS],
                    "source": path,
                    "chunk_id": str(hit.get("docid") or path or index),
                    "score": round(result_score(hit), 4),
                }
            )

        # Open the top pages for grounded text and exact citations. A page that
        # cannot be read keeps its search snippet instead of being dropped.
        for source in sources[:HYDRATE_LIMIT]:
            path = source["source"]
            if not path:
                continue
            try:
                text = await client.get(path, max_lines=None)
            except Exception as exc:
                self.logger.warning("wiki qmd could not read %s: %s", path, exc)
                continue
            if text:
                notes.append({"source": path, "title": source["title"], "content": text})

        parts = [
            f"[{index}] {note['source']}\n{note['content']}"
            for index, note in enumerate(notes, start=1)
        ]
        content = "\n\n".join(parts)
        return {
            "query": query,
            "answer": content,
            "content": content,
            "sources": sources,
            "provider": PROVIDER,
            "collections": collections,
            "notes": notes,
        }

    @staticmethod
    def _error(query: str, message: str, error_type: str) -> dict[str, Any]:
        return {
            "query": query,
            "answer": message,
            "content": "",
            "sources": [],
            "provider": PROVIDER,
            "error_type": error_type,
        }

    async def initialize(self, kb_name: str, file_paths: list[str], **kwargs: Any) -> bool:
        raise RuntimeError(
            "The wiki owns this index. Add markdown to the wiki and let qmd index it. "
            "The wiki provider only retrieves."
        )

    async def add_documents(self, kb_name: str, file_paths: list[str], **kwargs: Any) -> bool:
        return await self.initialize(kb_name, file_paths, **kwargs)

    async def delete(self, kb_name: str, **kwargs: Any) -> bool:
        # Only this KB's pointer goes; the owner's wiki is never deleted here.
        return True


__all__ = ["WikiQmdPipeline", "wiki_rag_enabled", "wiki_url"]
