"""An in-memory index over the notes: text search, tags, links and the link graph.

Reading every note on every request does not scale past a few hundred notes, so the index keeps
what it parsed from each file and re-reads only files whose size or modification time changed.
Rebuilding after a restart costs one pass over the notes; nothing is stored on disk.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from deeptutor.services.notes_links import LinkResolver, parse_links, parse_tags

_SNIPPET_BEFORE, _SNIPPET_AFTER = 60, 100
_TITLE_WEIGHT, _TAG_WEIGHT = 8, 4


@dataclass
class _Entry:
    stamp: tuple[int, int]
    text: str = ""
    lower: str = ""
    tags: list[str] = field(default_factory=list)
    links: list[str] = field(default_factory=list)


class NotesIndex:
    def __init__(self) -> None:
        self._entries: dict[str, _Entry] = {}
        self._names: dict[str, str] = {}  # path -> note name
        self._resolver = LinkResolver([])
        self._read_only: set[str] = set()

    def sync(self, notes: list[tuple[str, Path, bool]]) -> None:
        """Bring the index up to date with ``(name, path, read_only)`` rows for every note."""
        live: dict[str, _Entry] = {}
        names: dict[str, str] = {}
        for name, path, read_only in notes:
            key = str(path)
            try:
                st = path.stat()
            except OSError:
                continue
            stamp = (st.st_mtime_ns, st.st_size)
            entry = self._entries.get(key)
            if entry is None or entry.stamp != stamp:
                try:
                    text = path.read_text(encoding="utf-8", errors="replace")
                except OSError:
                    continue
                entry = _Entry(stamp, text, text.lower(), parse_tags(text), parse_links(text) if "[[" in text else [])
            live[key] = entry
            names[key] = name
            if read_only:
                self._read_only.add(name)
        self._entries, self._names = live, names
        self._read_only &= set(names.values())
        self._resolver = LinkResolver(names.values())

    def _rows(self):
        for key, entry in self._entries.items():
            yield self._names[key], entry

    def search(self, query: str, tag: str = "", limit: int = 50) -> list[dict[str, str]]:
        """Notes containing every word of ``query``, best first: title and tag hits outrank body hits."""
        words = [w for w in query.lower().split() if w]
        tag = tag.strip().lstrip("#").lower()
        if not words and not tag:
            return []
        scored: list[tuple[float, str, str]] = []
        for name, entry in self._rows():
            if tag and tag not in entry.tags:
                continue
            title = name.lower()
            if not all(w in entry.lower or w in title for w in words):
                continue
            score = 0.0
            for w in words:
                score += min(entry.lower.count(w), 20)
                if w in title:
                    score += _TITLE_WEIGHT
                if any(w in t for t in entry.tags):
                    score += _TAG_WEIGHT
            at = entry.lower.find(words[0]) if words else 0
            at = max(at, 0)
            snippet = " ".join(entry.text[max(0, at - _SNIPPET_BEFORE) : at + _SNIPPET_AFTER].split())
            scored.append((score, name, snippet))
        scored.sort(key=lambda row: (-row[0], row[1].lower()))
        return [{"name": name, "snippet": snippet} for _, name, snippet in scored[:limit]]

    def tags(self) -> list[dict[str, int | str]]:
        counts: dict[str, int] = {}
        for _, entry in self._rows():
            for tag in entry.tags:
                counts[tag] = counts.get(tag, 0) + 1
        return [{"tag": t, "count": c} for t, c in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))]

    def backlinks(self, note: str, limit: int = 100) -> list[dict[str, str]]:
        hits = []
        for name, entry in self._rows():
            if name == note or not entry.links:
                continue
            if any(self._resolver.resolve(target) == note for target in entry.links):
                at = max(entry.text.find("[["), 0)
                hits.append({"name": name, "snippet": " ".join(entry.text[max(0, at - _SNIPPET_BEFORE) : at + _SNIPPET_AFTER].split())})
                if len(hits) >= limit:
                    break
        return hits

    def graph(self, limit: int = 400) -> dict[str, list]:
        """Notes as nodes and resolved wikilinks as edges, keeping the most connected notes."""
        edges: dict[tuple[str, str], None] = {}
        degree: dict[str, int] = {}
        for name, entry in self._rows():
            for target in entry.links:
                other = self._resolver.resolve(target)
                if other and other != name and (name, other) not in edges:
                    edges[(name, other)] = None
                    degree[name] = degree.get(name, 0) + 1
                    degree[other] = degree.get(other, 0) + 1
        keep = {n for n, _ in sorted(degree.items(), key=lambda kv: (-kv[1], kv[0]))[:limit]}
        loose = [name for name, _ in self._rows() if name not in degree][: max(0, limit - len(keep))]
        keep.update(loose)
        return {
            "nodes": [
                {"id": n, "links": degree.get(n, 0), "read_only": n in self._read_only}
                for n in sorted(keep, key=str.lower)
            ],
            "edges": [{"source": a, "target": b} for (a, b) in edges if a in keep and b in keep],
        }


_INDEXES: dict[str, NotesIndex] = {}


def index_for(root: Path) -> NotesIndex:
    """The index for one notes folder (one per user in multi-user deployments)."""
    return _INDEXES.setdefault(str(root), NotesIndex())
