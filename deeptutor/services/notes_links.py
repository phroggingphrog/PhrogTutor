"""``[[wikilinks]]`` between Markdown notes: parsing, resolution and rename rewriting.

A link is ``[[target]]``, ``[[target|alias]]`` or ``[[target#heading]]``. The target is a note name
(``folder/note``) or just its last path segment (``note``); matching ignores case and a trailing
``.md``. When a bare name matches several notes the shallowest one wins, own notes before read-only
sources, then alphabetical. ``web/components/notes/wikilinks.ts`` mirrors :func:`resolve_link`.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

WIKILINK_RE = re.compile(r"\[\[([^\[\]|#\n]+?)(#[^\[\]|\n]*)?(\|[^\[\]\n]*)?\]\]")
_FENCE_RE = re.compile(r"(```.*?```|~~~.*?~~~|`[^`\n]*`)", re.S)


def _key(value: str) -> str:
    value = value.strip().strip("/")
    if value.lower().endswith(".md"):
        value = value[:-3]
    return value.lower()


def _rank(name: str) -> tuple[bool, int, str]:
    return (name.startswith("@"), name.count("/"), name.lower())


class LinkResolver:
    """Resolves many link targets against one list of note names in O(1) each."""

    def __init__(self, names: Iterable[str]) -> None:
        self._exact: dict[str, str] = {}
        self._tail: dict[str, str] = {}
        for name in names:
            key = _key(name)
            if key not in self._exact or _rank(name) < _rank(self._exact[key]):
                self._exact[key] = name
            tail = key.rsplit("/", 1)[-1]
            if tail not in self._tail or _rank(name) < _rank(self._tail[tail]):
                self._tail[tail] = name

    def resolve(self, target: str) -> str | None:
        wanted = _key(target)
        if not wanted:
            return None
        found = self._exact.get(wanted)
        if found is None and "/" not in wanted:
            found = self._tail.get(wanted)
        return found


def resolve_link(target: str, names: Iterable[str]) -> str | None:
    """The note a link target points at, or None when no note matches."""
    return LinkResolver(names).resolve(target)


_TAG_RE = re.compile(r"(?<![\w&/#])#([A-Za-z][\w/-]*)")


def parse_tags(text: str) -> list[str]:
    """``#tags`` in the text outside code, lowercased and de-duplicated. ``# Heading`` is not a tag."""
    seen: dict[str, None] = {}
    for chunk, skip in _split(text):
        if skip:
            continue
        for match in _TAG_RE.finditer(chunk):
            seen.setdefault(match.group(1).rstrip("/-").lower(), None)
    return [tag for tag in seen if tag]


def parse_links(text: str) -> list[str]:
    """Targets of every wikilink in ``text``, in order, skipping code."""
    return [
        m.group(1).strip()
        for chunk, skip in _split(text)
        if not skip
        for m in WIKILINK_RE.finditer(chunk)
    ]


def links_to(text: str, note: str, names: list[str]) -> bool:
    return any(resolve_link(target, names) == note for target in parse_links(text))


def rewrite_links(text: str, old: str, new: str, names_before: list[str]) -> str:
    """Point links that resolved to ``old`` at ``new``, keeping alias and heading.

    A link written with a folder keeps using the full new path; a bare-name link keeps a bare name
    unless that name would now be ambiguous.
    """
    names_after = [new if n == old else n for n in names_before]

    def fix(match: re.Match[str]) -> str:
        target = match.group(1).strip()
        if resolve_link(target, names_before) != old:
            return match.group(0)
        bare = new.rsplit("/", 1)[-1]
        unique = sum(_key(n).rsplit("/", 1)[-1] == _key(bare) for n in names_after) == 1
        use = bare if "/" not in target and unique else new
        return f"[[{use}{match.group(2) or ''}{match.group(3) or ''}]]"

    return "".join(WIKILINK_RE.sub(fix, chunk) if not skip else chunk for chunk, skip in _split(text))


def _split(text: str):
    pos = 0
    for match in _FENCE_RE.finditer(text):
        yield text[pos : match.start()], False
        yield match.group(0), True
        pos = match.end()
    yield text[pos:], False
