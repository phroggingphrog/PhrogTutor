"""Shared lower-level execution engine used by every application adapter."""

from __future__ import annotations

from collections.abc import AsyncIterator

from deeptutor.core.context import UnifiedContext
from deeptutor.core.stream import StreamEvent


class TurnEngine:
    """Execute an assembled context through the canonical orchestrator."""

    def __init__(self, capability_registry=None) -> None:  # noqa: ANN001
        self.capability_registry = capability_registry

    async def execute(self, context: UnifiedContext) -> AsyncIterator[StreamEvent]:
        from deeptutor.runtime.pi_turn_engine import pi_engine_enabled, run_pi_turn

        if pi_engine_enabled():  # PI_TUTOR_ENGINE=1: one pi SDK session per turn (pi_engine/turn.ts)
            async for event in run_pi_turn(context):
                yield event
            return
        # Lazy loading avoids provider/plugin import side effects at process
        # boot and leaves one stable patch point for tests and embedders.
        from deeptutor.runtime.orchestrator import ChatOrchestrator

        orchestrator = (
            ChatOrchestrator()
            if self.capability_registry is None
            else ChatOrchestrator(capability_registry=self.capability_registry)
        )
        async for event in orchestrator.handle(context):
            yield event


_default_engine: TurnEngine | None = None


def get_turn_engine() -> TurnEngine:
    global _default_engine
    if _default_engine is None:
        _default_engine = TurnEngine()
    return _default_engine


__all__ = ["TurnEngine", "get_turn_engine"]
