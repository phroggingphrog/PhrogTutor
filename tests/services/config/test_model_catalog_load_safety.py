from __future__ import annotations

import json
from pathlib import Path
import threading

from deeptutor.services.config.model_catalog import ModelCatalogService


def _profile(model: str) -> dict:
    return {"id": "p1", "name": "P1", "models": [{"id": "m1", "model": model}]}


def _embedding_models(catalog: dict) -> list[str]:
    profiles = catalog["services"]["embedding"]["profiles"]
    return [m["model"] for p in profiles for m in p.get("models", [])]


def test_unparseable_catalog_is_backed_up_before_defaults_replace_it(tmp_path: Path) -> None:
    path = tmp_path / "model_catalog.json"
    path.write_text('{"services": {"embedding": {"profiles": [', encoding="utf-8")

    catalog = ModelCatalogService(path).load()

    assert _embedding_models(catalog) == []
    backup = tmp_path / "model_catalog.json.unreadable"
    assert backup.read_text(encoding="utf-8").startswith('{"services"')


def test_valid_catalog_keeps_configured_embedding_models(tmp_path: Path) -> None:
    service = ModelCatalogService(tmp_path / "model_catalog.json")
    service.update(
        lambda catalog: catalog["services"]["embedding"]["profiles"].append(
            _profile("text-embedding-3-small")
        )
    )

    assert _embedding_models(service.load()) == ["text-embedding-3-small"]
    assert not (tmp_path / "model_catalog.json.unreadable").exists()


def test_concurrent_loads_do_not_drop_a_saved_embedding_model(tmp_path: Path) -> None:
    service = ModelCatalogService(tmp_path / "model_catalog.json")
    service.update(
        lambda catalog: catalog["services"]["embedding"]["profiles"].append(_profile("bge-m3"))
    )
    results: list[list[str]] = []
    errors: list[BaseException] = []

    def reader() -> None:
        try:
            for _ in range(25):
                results.append(_embedding_models(service.load()))
        except BaseException as exc:  # pragma: no cover - failure path
            errors.append(exc)

    threads = [threading.Thread(target=reader) for _ in range(6)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert not errors
    assert results and all(models == ["bge-m3"] for models in results)
    on_disk = json.loads((tmp_path / "model_catalog.json").read_text(encoding="utf-8"))
    assert _embedding_models(on_disk) == ["bge-m3"]
