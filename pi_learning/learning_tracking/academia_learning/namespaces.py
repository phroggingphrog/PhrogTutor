"""The three Academia RAG namespaces.

Academia selects exactly one namespace per retrieval; it must not search
everything by default. ``MAIN_HERMES`` is intentionally not implemented by
this package -- callers who need it must go to baseline.db / canonical.db /
project_docs / the Obsidian vault directly. Keeping it enumerated (but
unimplemented) here means any code path that tries to route a "main hermes"
question through this package fails loudly instead of silently searching
university/learner data it has no business touching.
"""

from __future__ import annotations

from enum import Enum


class Namespace(str, Enum):
    UNIVERSITY_COURSE = "UNIVERSITY_COURSE"
    LEARNER_EVIDENCE = "LEARNER_EVIDENCE"
    MAIN_HERMES = "MAIN_HERMES"


class UnsupportedNamespaceError(RuntimeError):
    """Raised when a caller asks this package to serve MAIN_HERMES content."""


def require_supported(namespace: Namespace) -> None:
    if namespace is Namespace.MAIN_HERMES:
        raise UnsupportedNamespaceError(
            "MAIN_HERMES is out of scope for academia_learning; use "
            "baseline.db (namespace-gated), canonical.db, project_docs, or "
            "the Obsidian vault directly. Do not route it through the "
            "university/learner-evidence store."
        )
