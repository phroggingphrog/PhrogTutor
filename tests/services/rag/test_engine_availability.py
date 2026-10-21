"""A retrieval engine whose package is not installed is reported with its install command."""

from __future__ import annotations

import importlib.util

from fastapi import HTTPException
import pytest

from deeptutor.services.rag import availability, factory


@pytest.fixture()
def without(monkeypatch):
    """Pretend the given top-level packages are not installed."""

    def hide(*names: str) -> None:
        real = importlib.util.find_spec

        def find_spec(name, *args, **kwargs):
            return None if name.split(".")[0] in names else real(name, *args, **kwargs)

        monkeypatch.setattr(importlib.util, "find_spec", find_spec)

    return hide


def test_engines_without_an_optional_package_are_always_ready() -> None:
    for provider in ("wiki", "kiwix", "lightrag-server", "weknora", "ima"):
        assert availability.engine_installed(provider)
        availability.require_engine(provider)


def test_a_missing_package_is_named_with_its_install_command(without) -> None:
    without("llama_index")
    assert not availability.engine_installed("llamaindex")
    with pytest.raises(availability.RagDependencyMissing, match=r"deeptutor\[rag-llamaindex\]"):
        availability.require_engine("llamaindex")
    assert availability.install_hint("pageindex-oss") == "pip install 'deeptutor[rag-pageindex]'"


def test_the_provider_list_says_so_instead_of_claiming_it_works(without) -> None:
    without("llama_index", "pageindex")
    by_id = {entry["id"]: entry for entry in factory.list_pipelines()}
    assert by_id["llamaindex"]["configured"] is False
    assert "rag-llamaindex" in by_id["llamaindex"]["description"]
    assert by_id["pageindex"]["configured"] is False
    assert by_id["pageindex-oss"]["configured"] is False
    assert "rag-pageindex" in by_id["pageindex-oss"]["readiness_reason"]
    assert by_id["lightrag-server"]["configured"] is True  # a thin HTTP client: nothing to install


def test_building_a_pipeline_fails_with_the_install_hint_not_a_module_error(without, tmp_path) -> None:
    without("llama_index")
    with pytest.raises(availability.RagDependencyMissing, match="not installed"):
        factory._build_pipeline("llamaindex", str(tmp_path))


def test_creating_a_knowledge_base_on_a_missing_engine_is_a_clear_400(without) -> None:
    from deeptutor.api.routers.knowledge import _assert_provider_ready

    without("llama_index")
    with pytest.raises(HTTPException) as caught:
        _assert_provider_ready("llamaindex")
    assert caught.value.status_code == 400 and "rag-llamaindex" in caught.value.detail
