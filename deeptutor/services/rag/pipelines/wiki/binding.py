"""Which attached knowledge bases are connected wikis (read at mount time)."""

from __future__ import annotations

from typing import Any


def wiki_kb_entry(kb_name: str | None) -> dict[str, Any] | None:
    """Return the stored pointer for a connected wiki KB, or None."""
    if not kb_name:
        return None
    try:
        from deeptutor.multi_user.knowledge_access import resolve_kb
        from deeptutor.services.rag.provider_binding import load_kb_config_entry

        resource = resolve_kb(kb_name, require_write=False)
        entry = load_kb_config_entry(resource.base_dir, resource.name)
    except Exception:
        return None
    return entry if entry.get("type") == "wiki" else None


def is_wiki_kb(kb_name: str | None) -> bool:
    return wiki_kb_entry(kb_name) is not None
