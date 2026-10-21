"""Shared fakes for tests that drive a chat turn through the pi loop with a scripted model."""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from deeptutor.agents.chat.agentic_pipeline import AgenticChatPipeline
from deeptutor.core.context import UnifiedContext
from deeptutor.core.stream import StreamEvent
from deeptutor.core.tool_protocol import ToolResult
from deeptutor.runtime.stream_bus import StreamBus


async def collect_bus_events(bus: StreamBus) -> tuple[list[StreamEvent], asyncio.Task[Any]]:
    events: list[StreamEvent] = []

    async def _consume() -> None:
        async for event in bus.subscribe():
            events.append(event)

    consumer = asyncio.create_task(_consume())
    await asyncio.sleep(0)
    return events, consumer  # type: ignore[return-value]


def llm_chunk(
    *,
    content: str | None = None,
    tool_calls: list[dict[str, Any]] | None = None,
    usage: Any = None,
    finish_reason: str | None = None,
    reasoning_content: str | None = None,
    provider_specific_fields: dict[str, Any] | None = None,
) -> SimpleNamespace:
    delta_fields: dict[str, Any] = {"content": content}
    if reasoning_content is not None:
        delta_fields["reasoning_content"] = reasoning_content
    if tool_calls is not None:
        delta_fields["tool_calls"] = [
            SimpleNamespace(
                index=tc.get("index", i),
                id=tc.get("id"),
                extra_content=tc.get("extra_content"),
                function=SimpleNamespace(
                    name=tc.get("name"),
                    arguments=tc.get("arguments"),
                ),
            )
            for i, tc in enumerate(tool_calls)
        ]
    else:
        delta_fields["tool_calls"] = None
    return SimpleNamespace(
        choices=[
            SimpleNamespace(
                delta=SimpleNamespace(**delta_fields),
                finish_reason=finish_reason,
                provider_specific_fields=provider_specific_fields,
            )
        ],
        usage=usage,
    )


async def async_llm_stream(chunks: list[SimpleNamespace]):
    for chunk in chunks:
        yield chunk


class ScriptedChatClient:
    def __init__(self, scripted: list[list[SimpleNamespace]]) -> None:
        self._script = list(scripted)
        self.call_count = 0
        self.calls: list[dict[str, Any]] = []

        class _Completions:
            def __init__(self, parent: ScriptedChatClient) -> None:
                self.parent = parent

            async def create(self, **kwargs):
                self.parent.call_count += 1
                self.parent.calls.append({**kwargs, "messages": list(kwargs.get("messages") or [])})
                if not self.parent._script:
                    raise RuntimeError("Scripted client exhausted")
                return async_llm_stream(self.parent._script.pop(0))

        class _Chat:
            def __init__(self, parent: ScriptedChatClient) -> None:
                self.completions = _Completions(parent)

        self.chat = _Chat(self)


class Registry:
    def __init__(self) -> None:
        self.executed: list[dict[str, Any]] = []

    def deferred_tools(self):
        return []

    def build_prompt_text(self, _enabled, **_kwargs):
        return "- `web_search` - Search the web"

    def build_openai_schemas(self, _enabled):
        return [
            {
                "type": "function",
                "function": {
                    "name": "web_search",
                    "description": "Search",
                    "parameters": {
                        "type": "object",
                        "properties": {"query": {"type": "string"}},
                        "required": ["query"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "ask_user",
                    "description": "Ask the user",
                    "parameters": {
                        "type": "object",
                        "properties": {"questions": {"type": "array"}},
                        "required": ["questions"],
                    },
                },
            },
        ]

    async def execute(self, name: str, **kwargs):
        self.executed.append({"name": name, "kwargs": kwargs})
        return ToolResult(
            content="tool answer",
            sources=[{"tool": name}],
            metadata={"tool": name},
            success=True,
        )


@pytest.fixture(autouse=True)
def isolated_runtime(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from deeptutor.capabilities.setup.binding import mark_intro_shown
    from deeptutor.services.path_service import PathService

    # Exercise the loop in an initialized install, independent of developer
    # data or whether another test has already created the default workspace.
    monkeypatch.setenv("DEEPTUTOR_HOME", str(tmp_path))
    paths = PathService(workspace_root=tmp_path / "data")
    monkeypatch.setattr(PathService, "_instance", paths)
    paths.ensure_all_directories()
    # First-run onboarding legitimately changes the next turn's system prompt.
    # Replay tests require stable configuration; setup owns onboarding tests.
    mark_intro_shown()
    monkeypatch.setattr(
        "deeptutor.agents.loop.pipeline.get_llm_config",
        lambda: SimpleNamespace(
            binding="openai",
            model="gpt-test",
            api_key="k",
            base_url="u",
            api_version=None,
            extra_headers={},
            reasoning_effort=None,
        ),
    )


async def run_turn(pipeline: AgenticChatPipeline, context: UnifiedContext):
    bus = StreamBus()
    events, consumer = await collect_bus_events(bus)
    await pipeline.run(context, bus)
    await asyncio.sleep(0)
    await bus.close()
    await consumer
    return events
