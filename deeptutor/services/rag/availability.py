"""Which retrieval engines are installed.

The LlamaIndex stack and the PageIndex SDK are optional extras (``rag-llamaindex``, ``rag-pageindex``):
together they are about 300 MB of the install, and a deployment that retrieves from a wiki or a
connected server never loads them. Every engine that needs one is checked here, so a missing package
is reported with the command that fixes it instead of surfacing as ``No module named 'llama_index'``.
"""

from __future__ import annotations

import importlib.util

#: provider id -> (importable module, extra that installs it)
_REQUIRES: dict[str, tuple[str, str]] = {
    "llamaindex": ("llama_index.core", "rag-llamaindex"),
    "pageindex": ("pageindex", "rag-pageindex"),
    "pageindex-oss": ("pageindex", "rag-pageindex"),
}


class RagDependencyMissing(RuntimeError):
    """The engine's package is not installed; the message says how to install it."""


def _installed(module: str) -> bool:
    try:
        return importlib.util.find_spec(module) is not None
    except (ImportError, ValueError):
        return False


def engine_installed(provider: str) -> bool:
    need = _REQUIRES.get(provider)
    return need is None or _installed(need[0])


def install_hint(provider: str) -> str:
    need = _REQUIRES.get(provider)
    return f"pip install 'deeptutor[{need[1]}]'" if need else ""


def require_engine(provider: str) -> None:
    """Raise :class:`RagDependencyMissing` when ``provider`` needs a package that is not installed."""
    if not engine_installed(provider):
        raise RagDependencyMissing(
            f"The {provider} retrieval engine is not installed. Run `{install_hint(provider)}` on the server, "
            "then restart."
        )
