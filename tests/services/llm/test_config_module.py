"""Tests for LLM configuration helpers."""

from __future__ import annotations

import os

import pytest

from deeptutor.services.config.provider_runtime import ResolvedLLMConfig
from deeptutor.services.llm import config as config_module
from deeptutor.services.llm.config import LLMConfig
from deeptutor.services.llm.exceptions import LLMConfigError


def _reset_config_cache() -> None:
    config_module._LLM_CONFIG_CACHE = None


def test_get_llm_config_from_resolver(monkeypatch) -> None:
    """Resolver-backed loading should populate provider metadata."""
    _reset_config_cache()

    def _fake_resolver(*_args, **_kwargs) -> ResolvedLLMConfig:
        return ResolvedLLMConfig(
            model="openai/gpt-4o-mini",
            provider_name="openrouter",
            provider_mode="gateway",
            binding_hint="openrouter",
            binding="openrouter",
            api_key="sk-or-test",
            base_url="https://openrouter.ai/api/v1",
            effective_url="https://openrouter.ai/api/v1",
            api_version=None,
            extra_headers={"X-Test": "1"},
            reasoning_effort="medium",
            context_window=128000,
        )

    monkeypatch.setattr(config_module, "resolve_llm_runtime_config", _fake_resolver)
    config = config_module.get_llm_config()

    assert isinstance(config, LLMConfig)
    assert config.model == "openai/gpt-4o-mini"
    assert config.provider_name == "openrouter"
    assert config.provider_mode == "gateway"
    assert config.base_url == "https://openrouter.ai/api/v1"
    assert config.extra_headers == {"X-Test": "1"}
    assert config.reasoning_effort == "medium"
    assert config.context_window == 128000


def test_get_llm_config_raises_when_resolver_fails(monkeypatch) -> None:
    _reset_config_cache()
    monkeypatch.setattr(
        config_module,
        "resolve_llm_runtime_config",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("boom")),
    )

    with pytest.raises(RuntimeError, match="boom"):
        config_module.get_llm_config()


def test_scoped_llm_config_takes_precedence_over_global_cache(monkeypatch) -> None:
    _reset_config_cache()
    monkeypatch.setattr(
        config_module,
        "resolve_llm_runtime_config",
        lambda *_args, **_kwargs: ResolvedLLMConfig(
            model="gpt-global",
            provider_name="openai",
            provider_mode="standard",
            binding_hint="openai",
            binding="openai",
            api_key="sk-global",
            base_url="https://global.example/v1",
            effective_url="https://global.example/v1",
            api_version=None,
            extra_headers={},
            reasoning_effort=None,
            context_window=None,
        ),
    )
    global_cfg = config_module.get_llm_config()
    scoped_cfg = global_cfg.model_copy(update={"model": "gpt-scoped"})

    token = config_module.set_scoped_llm_config(scoped_cfg)
    try:
        assert config_module.get_llm_config().model == "gpt-scoped"
    finally:
        config_module.reset_scoped_llm_config(token)

    assert config_module.get_llm_config().model == "gpt-global"


def test_resolver_missing_model_raises(monkeypatch) -> None:
    _reset_config_cache()

    monkeypatch.setattr(
        config_module,
        "resolve_llm_runtime_config",
        lambda *_args, **_kwargs: ResolvedLLMConfig(
            model="",
            provider_name="openai",
            provider_mode="standard",
            binding_hint="openai",
            binding="openai",
            api_key="test-key",
            base_url="https://example.com/v1",
            effective_url="https://example.com/v1",
            api_version=None,
            extra_headers={},
            reasoning_effort=None,
            context_window=None,
        ),
    )
    with pytest.raises(LLMConfigError):
        config_module.get_llm_config()


def test_official_openai_without_real_key_raises(monkeypatch) -> None:
    _reset_config_cache()
    monkeypatch.setattr(
        config_module,
        "resolve_llm_runtime_config",
        lambda *_args, **_kwargs: ResolvedLLMConfig(
            model="gpt-4o-mini",
            provider_name="openai",
            provider_mode="standard",
            binding_hint="openai",
            binding="openai",
            api_key="",
            base_url="https://api.openai.com/v1",
            effective_url="https://api.openai.com/v1",
            api_version=None,
            extra_headers={},
            reasoning_effort=None,
            context_window=None,
        ),
    )

    with pytest.raises(LLMConfigError, match="OpenAI API key is not configured"):
        config_module.get_llm_config()


def test_profile_api_key_list_resolves_without_crash(monkeypatch) -> None:
    """A profile api_key configured as a key array must resolve (PR #962).

    Regression: the upstream refactor wrote the placeholder check as
    ``resolved.api_key in {...}``, which raises ``TypeError: unhashable type:
    'list'`` the moment a profile carries a key pool.
    """
    _reset_config_cache()
    keys = ["sk-one", "sk-two", "sk-three", "sk-four"]

    monkeypatch.setattr(
        config_module,
        "resolve_llm_runtime_config",
        lambda *_args, **_kwargs: ResolvedLLMConfig(
            model="doubao-seed-1-6",
            provider_name="openai_compat_test",
            provider_mode="standard",
            binding_hint="openai_compat_test",
            binding="openai_compat_test",
            api_key=list(keys),
            base_url="https://example.com/v1",
            effective_url="https://example.com/v1",
            api_version=None,
            extra_headers={},
            reasoning_effort=None,
            context_window=None,
        ),
    )

    config = config_module.get_llm_config()
    assert config.api_key == keys
    assert config.get_api_key() == "sk-one"


def test_placeholder_detection_uses_primary_key_of_list(monkeypatch) -> None:
    """A list whose keys are all placeholders still counts as unconfigured."""
    _reset_config_cache()

    monkeypatch.setattr(
        config_module,
        "resolve_llm_runtime_config",
        lambda *_args, **_kwargs: ResolvedLLMConfig(
            model="gpt-4o-mini",
            provider_name="openai",
            provider_mode="standard",
            binding_hint="openai",
            binding="openai",
            api_key=[""],
            base_url="https://api.openai.com/v1",
            effective_url="https://api.openai.com/v1",
            api_version=None,
            extra_headers={},
            reasoning_effort=None,
            context_window=None,
        ),
    )

    with pytest.raises(LLMConfigError, match="OpenAI API key is not configured"):
        config_module.get_llm_config()


def _catalog_with_profiles() -> dict:
    return {
        "services": {
            "llm": {
                "active_profile_id": "prof-active-weak",
                "active_model_id": "model-active-weak-1",
                "profiles": [
                    {
                        "id": "prof-active-weak",
                        "binding": "openai_compat_test",
                        "models": [{"id": "model-active-weak-1", "model": "glmfree-air"}],
                    },
                    {
                        "id": "prof-strong",
                        "binding": "openai_compat_test",
                        "active_model_id": "model-strong-2",
                        "models": [
                            {"id": "model-strong-1", "model": "doubao-seed-other"},
                            {"id": "model-strong-2", "model": "doubao-seed-1-6"},
                        ],
                    },
                ],
            }
        }
    }


def _patch_catalog(monkeypatch, catalog: dict | None) -> None:
    from deeptutor.services.config import model_catalog as mc_module

    class _FakeService:
        def load(self):
            if catalog is None:
                raise RuntimeError("catalog unavailable")
            return catalog

    monkeypatch.setattr(mc_module, "get_model_catalog_service", lambda: _FakeService())


def _resolved_for_selection(selection: dict | None) -> ResolvedLLMConfig:
    """Stand-in resolver: model from the selection, else the weak active."""
    if selection is None:
        model = "glmfree-air"
    elif selection["profile_id"] == "prof-strong" and selection["model_id"] == "model-strong-2":
        model = "doubao-seed-1-6"
    else:
        raise AssertionError(f"unexpected selection payload: {selection}")
    return ResolvedLLMConfig(
        model=model,
        provider_name="openai_compat_test",
        provider_mode="standard",
        binding_hint="openai_compat_test",
        binding="openai_compat_test",
        api_key="sk-test",
        base_url="https://example.com/v1",
        effective_url="https://example.com/v1",
        api_version=None,
        extra_headers={},
        reasoning_effort=None,
        context_window=None,
    )
