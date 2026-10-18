import os
import re
from dataclasses import dataclass
from pathlib import Path

from .concepts import GENERIC_TITLE_TERMS


_BLOCKED = (".env", ".git", "auth", "credential", "cache", "logs", "raw", "canonical")
_RELEVANT_PATH_TERMS = {"analytics", "data", "model", "statistics", "sql", "audit"}


@dataclass(frozen=True)
class Example:
    path: Path
    excerpt: str
    score: int


def _blocked(path: Path) -> bool:
    return any(word in part.lower() for part in path.parts for word in _BLOCKED)


def _terms(value: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", value.lower()))


def _excerpt(text: str, terms: set[str]) -> str:
    best_start = best_matches = 0
    offset = 0
    for line in text.splitlines(keepends=True) or [text]:
        matches = len(_terms(line) & terms)
        if matches > best_matches:
            best_start, best_matches = offset, matches
        offset += len(line)
    line = text[best_start:text.find("\n", best_start) if "\n" in text[best_start:] else len(text)]
    match = next((item for item in re.finditer(r"[a-z0-9]+", line.lower()) if item.group() in terms), None)
    focus = best_start + (match.start() if match else 0)
    start = max(0, focus - 250)
    end = min(len(text), start + 500)
    start = max(0, end - 500)
    return text[start:end].strip()


def find_example(
    query: str,
    roots: list[Path],
    *,
    max_files: int = 200,
    max_bytes: int = 1_000_000,
) -> Example | None:
    if max_files <= 0 or max_bytes <= 0:
        return None
    terms = _terms(query) - GENERIC_TITLE_TERMS
    best = None
    seen_files = seen_bytes = 0
    for root_arg in roots:
        root = Path(root_arg).resolve()
        if not root.is_dir() or _blocked(Path(root.name)):
            continue
        for directory, directories, files in os.walk(root):
            directories[:] = sorted(name for name in directories if not _blocked(Path(name)))
            for name in sorted(files):
                if seen_files >= max_files or seen_bytes >= max_bytes:
                    return best
                seen_files += 1
                path = Path(directory, name)
                if _blocked(path.relative_to(root)):
                    continue
                try:
                    resolved = path.resolve()
                    if not resolved.is_relative_to(root):
                        continue
                    with resolved.open("rb") as source:
                        content = source.read(max_bytes - seen_bytes)
                except OSError:
                    continue
                seen_bytes += len(content)
                if b"\0" in content:
                    continue
                try:
                    text = content.decode("utf-8")
                except UnicodeDecodeError:
                    continue
                matches = _terms(text) & terms
                score = len(matches) + len(_terms(str(path.relative_to(root))) & _RELEVANT_PATH_TERMS)
                if matches and (
                    best is None
                    or score > best.score
                    or (score == best.score and str(resolved) < str(best.path))
                ):
                    best = Example(resolved, _excerpt(text, terms), score)
    return best
