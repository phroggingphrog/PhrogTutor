"""Bounded MCP client for the local wiki qmd server (retrieval only).

One HTTP endpoint (default ``http://127.0.0.1:8906/mcp``) exposes qmd's
``query`` and ``get`` tools over MCP streamable HTTP. This is the same server
``pi_engine/turn.ts`` reaches through ``agent-dir/mcp.json`` as ``llm-wiki``.

Nothing is uploaded, chunked or embedded here: qmd already indexes the owner's
markdown, and this client only reads results.
"""

from __future__ import annotations

import json
import os
from typing import Any
from urllib.parse import urlsplit

import httpx

PROVIDER = "wiki"

DEFAULT_URL = "http://127.0.0.1:8906/mcp"

# qmd's own collections. The owner's LEARNING-MAP names two retrieval
# namespaces; neither exists as a qmd collection, so each maps onto the
# collection that actually holds that material.
NAMESPACE_COLLECTIONS: dict[str, str] = {
    "university-course": "university",
    "learner-evidence": "personal-learning",
}

DEFAULT_NAMESPACES = ("university-course", "learner-evidence")

MAX_RESPONSE_BYTES = 2_000_000
MAX_FIELD_CHARS = 200
MAX_GET_CHARS = 20_000


class WikiQmdError(ValueError):
    """The wiki qmd server cannot satisfy a request."""


def normalize_endpoint(value: str) -> str:
    raw = str(value or "").strip()
    if len(raw) > 2048 or any(ord(char) < 32 for char in raw):
        raise WikiQmdError("Enter a valid wiki qmd MCP URL.")
    parsed = urlsplit(raw)
    try:
        port = parsed.port
    except ValueError as exc:
        raise WikiQmdError("Enter a valid wiki qmd host and port.") from exc
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or (port is not None and not 1 <= port <= 65535)
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        raise WikiQmdError("Enter an HTTP(S) MCP URL without credentials or query parameters.")
    return parsed.geturl()


def collection_for_namespace(namespace: str, default: str | None = None) -> str:
    """Map a LEARNING-MAP namespace to the qmd collection that holds it."""
    name = str(namespace or "").strip()
    if not name:
        raise WikiQmdError("A namespace name is required.")
    return NAMESPACE_COLLECTIONS.get(name, default or name)


def resolve_collections(namespaces: Any = None) -> list[str]:
    """Return the qmd collections to search for the given namespaces.

    ``PI_TUTOR_WIKI_COLLECTIONS`` (comma-separated) wins when set, so an
    operator can widen or narrow the search without a code change.
    """
    override = (os.environ.get("PI_TUTOR_WIKI_COLLECTIONS") or "").strip()
    if override:
        return [part.strip() for part in override.split(",") if part.strip()]
    if namespaces is None:
        namespaces = DEFAULT_NAMESPACES
    if isinstance(namespaces, str):
        namespaces = [part.strip() for part in namespaces.split(",") if part.strip()]
    seen: list[str] = []
    for namespace in namespaces:
        collection = collection_for_namespace(str(namespace))
        if collection not in seen:
            seen.append(collection)
    return seen


def _parse_jsonrpc(body: str) -> dict[str, Any]:
    """Read a JSON-RPC result from a plain-JSON or SSE-framed body."""
    text = body.strip()
    if not text:
        raise WikiQmdError("The wiki qmd server returned an empty response.")
    if text.startswith("{"):
        try:
            return json.loads(text)
        except ValueError as exc:
            raise WikiQmdError("The wiki qmd server returned invalid JSON.") from exc
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("data:"):
            continue
        payload = line[5:].strip()
        if not payload or payload == "[DONE]":
            continue
        try:
            return json.loads(payload)
        except ValueError:
            continue
    raise WikiQmdError("The wiki qmd server returned no JSON-RPC payload.")


def _blocks_text(result: dict[str, Any]) -> str:
    """Join every text-carrying content block (qmd answers in text or resource)."""
    parts: list[str] = []
    for block in result.get("content") or []:
        if not isinstance(block, dict):
            continue
        if isinstance(block.get("text"), str):
            parts.append(block["text"])
        resource = block.get("resource")
        if isinstance(resource, dict) and isinstance(resource.get("text"), str):
            parts.append(resource["text"])
    return "\n".join(part for part in parts if part)


class WikiQmdClient:
    """Minimal JSON-RPC over HTTP client for qmd's ``query`` and ``get`` tools."""

    def __init__(
        self,
        url: str = DEFAULT_URL,
        *,
        timeout: float = 300.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.url = normalize_endpoint(url)
        self.timeout = float(timeout)
        self.transport = transport
        self._next_id = 1

    def _client(self) -> httpx.AsyncClient:
        # qmd answers in milliseconds when warm and can spend a minute loading
        # its embedding model when cold, so the read timeout is generous.
        return httpx.AsyncClient(
            transport=self.transport,
            timeout=httpx.Timeout(self.timeout, connect=10.0),
            follow_redirects=False,
            trust_env=False,
        )

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """Call one MCP tool and return its ``result`` object."""
        request_id = self._next_id
        self._next_id += 1
        payload = {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments},
        }
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }
        try:
            async with self._client() as client:
                response = await client.post(self.url, headers=headers, json=payload)
        except httpx.HTTPError as exc:
            raise WikiQmdError("Could not reach the wiki qmd server.") from exc
        if response.status_code != 200:
            raise WikiQmdError(f"The wiki qmd server returned HTTP {response.status_code}.")
        if len(response.content) > MAX_RESPONSE_BYTES:
            raise WikiQmdError("The wiki qmd server response exceeded the size limit.")
        message = _parse_jsonrpc(response.text)
        if message.get("error"):
            error = message["error"]
            detail = error.get("message") if isinstance(error, dict) else str(error)
            raise WikiQmdError(f"The wiki qmd server rejected the {name} call: {detail}")
        result = message.get("result")
        if not isinstance(result, dict):
            raise WikiQmdError(f"The wiki qmd server returned no result for {name}.")
        return result

    async def query(
        self,
        query: str,
        *,
        collections: list[str] | None = None,
        limit: int = 5,
        intent: str | None = None,
    ) -> dict[str, Any]:
        """Search the wiki. Returns qmd's ``structuredContent`` plus the raw text."""
        arguments: dict[str, Any] = {"query": query, "limit": limit}
        if collections:
            arguments["collections"] = list(collections)
        if intent:
            arguments["intent"] = intent
        result = await self.call_tool("query", arguments)
        structured = result.get("structuredContent")
        return {
            "text": _blocks_text(result),
            "results": list((structured or {}).get("results") or []),
        }

    async def get(self, file: str, *, max_lines: int | None = None) -> str:
        """Read one wiki page by path or docid."""
        arguments: dict[str, Any] = {"file": file}
        if max_lines:
            arguments["maxLines"] = max_lines
        result = await self.call_tool("get", arguments)
        return _blocks_text(result)[:MAX_GET_CHARS]

    async def status(self) -> str:
        """Read the index status (collections and doc counts)."""
        result = await self.call_tool("status", {})
        return _blocks_text(result)


def result_file(result: dict[str, Any]) -> str:
    """Return a result's wiki path, tolerating qmd's field naming."""
    for key in ("file", "path", "file_path"):
        value = result.get(key)
        if isinstance(value, str) and value:
            return value
    docid = result.get("docid")
    return docid if isinstance(docid, str) else ""


def result_title(result: dict[str, Any], fallback: str) -> str:
    title = result.get("title")
    if isinstance(title, str) and title.strip():
        return title.strip()[:MAX_FIELD_CHARS]
    return fallback


def result_score(result: dict[str, Any], fallback: float = 0.0) -> float:
    score = result.get("score")
    try:
        return float(score)
    except (TypeError, ValueError):
        return fallback


__all__ = [
    "PROVIDER",
    "DEFAULT_URL",
    "DEFAULT_NAMESPACES",
    "NAMESPACE_COLLECTIONS",
    "WikiQmdClient",
    "WikiQmdError",
    "collection_for_namespace",
    "normalize_endpoint",
    "resolve_collections",
    "result_file",
    "result_score",
    "result_title",
    "MAX_RESPONSE_BYTES",
    "MAX_GET_CHARS",
]
