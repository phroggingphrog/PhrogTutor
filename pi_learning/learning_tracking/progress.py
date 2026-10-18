import json
from collections.abc import Mapping
from pathlib import Path


def append_attempt(path: Path, attempt: Mapping) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as target:
        target.write(json.dumps(dict(attempt), separators=(",", ":")) + "\n")


def derive_mastery(path: Path) -> dict[str, dict]:
    mastery: dict[str, dict] = {}
    if not path.exists():
        return mastery
    with path.open(encoding="utf-8") as source:
        for line in source:
            record = json.loads(line)
            evidence = record.get("evidence", {})
            if not isinstance(evidence, dict):
                raise ValueError("progress evidence must be an object")
            for concept, dimensions in evidence.items():
                if not isinstance(dimensions, dict):
                    raise ValueError("concept evidence must be an object")
                mastery.setdefault(concept, {}).update(dimensions)
    return mastery
