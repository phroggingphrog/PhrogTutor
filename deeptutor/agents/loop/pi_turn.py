"""One chat turn on the pi engine (``pi_engine/loop.ts``).

The round rules (budgets, settlement, truncation and reasoning-only recovery,
finish redirects and overrides, ask_user pauses, terminators, context
checkpoints, retries) live in loop.ts. This module is DeepTutor's side of the
pipe: it builds the turn's first context, renders each pi round to the
StreamBus exactly as the reader expects, and answers loop.ts's asks with the
pipeline's capability hooks and tool dispatch.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import json
import logging
import os
from time import monotonic
from typing import TYPE_CHECKING, Any

from openai.types.chat import ChatCompletionChunk

from deeptutor.agents._shared.capability_result import emit_capability_result
from deeptutor.agents.loop.ask_user_drafts import AskUserDraftEmitter
from deeptutor.agents.loop.context_budget import LLMRequestSnapshot
from deeptutor.agents.loop.dsml_tool_calls import DSMLStreamFilter, extract_dsml_tool_calls
from deeptutor.core.context import UnifiedContext
from deeptutor.core.trace import build_trace_metadata, merge_trace_metadata, new_call_id
from deeptutor.runtime.agentic.messages import (
    assistant_message_with_tool_calls,
    extend_transient_model_messages,
    image_content_hashes,
    with_transient_model_messages,
)
from deeptutor.runtime.agentic.think_stream import InlineThinkFilter
from deeptutor.runtime.agentic.tool_call_stream import ToolCallAccumulator
from deeptutor.runtime.agentic.usage import message_content_chars, record_streamed_usage
from deeptutor.runtime.stream_bus import StreamBus
from deeptutor.services.llm import (
    LLMProviderTransportError,
    LLMReasoningBudgetExhausted,
    clean_thinking_tags,
    finish_was_truncated,
    pi_bridge,
)
from deeptutor.services.llm.image_replay import deduplicate_user_images
from deeptutor.services.llm.metrics import CallMeasurement, measurement_active
from deeptutor.services.llm.multimodal import has_image_parts
from deeptutor.services.llm.usage_frame import usage_breakdown
from deeptutor.services.llm.utils import unreachable_endpoint_hint
from deeptutor.services.session.provider_response_state import normalize_provider_response_state

if TYPE_CHECKING:  # pragma: no cover
    from deeptutor.agents.loop.pipeline import AgenticLoopPipeline

logger = logging.getLogger(__name__)

LOOP_STAGE = "responding"

# Notices loop.ts raises by key (localized through the pipeline's prompt pack).
_NOTICES = {
    "loop_settlement": "Exploration budget reached; completing required follow-up before the final answer.",
    "loop_budget_exhausted": "Exploration budget reached; answering with what has been gathered.",
    "loop_error_finish": "A step failed ({error}); answering with what has been gathered.",
    "capability_finish_rejected": "The model did not complete the required interactive step. Please retry the turn.",
    "provider_retry": "The model provider connection was interrupted; retrying.",
    "output_truncated": "The model output reached its token limit; asked it to continue.",
    "empty_finish_nudged": "The round produced only internal reasoning; asked the model to continue.",
    "tool_call_truncated": (
        "The model reached its output token limit while writing a tool call, so the call may be incomplete. "
        "If this repeats, raise this capability's max tokens or pick a model that reasons less."
    ),
}
# Loop directives loop.ts appends to the conversation.
_TEXTS = {
    "continue_truncated": (
        "Your previous response stopped at the token limit. Continue from where it ended without "
        "repeating it, and complete the user-facing answer."
    ),
    "continue_truncated_reasoning": (
        "Your previous round spent its entire output budget on internal reasoning and hit the token limit "
        "before writing anything — so there is nothing to continue from, and reasoning it through again "
        "will end the same way. Act now instead: make the tool call, or write the answer with the judgement "
        "you already have. Good enough is required; optimal is not. Do not redesign or second-guess what "
        "you had already settled on."
    ),
    "finish_empty_nudge": (
        "Your previous round produced only internal reasoning — no tool call and no user-facing answer. "
        "Continue now: either call the tools to execute your plan, or write the final user-facing answer directly."
    ),
    "repeat_reasoning_only_nudge": (
        "Your previous rounds produced only internal reasoning — no tool call and no user-facing answer. "
        "Do not reason further. Select the most likely next action and produce it now."
    ),
}


@dataclass(slots=True)
class LoopOutcome:
    final_text: str = ""
    completed: bool = False
    provider_response_state: dict[str, Any] | None = None


def _provider_state(items: list[dict[str, Any]], reasoning: str, blocks: list[dict[str, Any]]) -> dict[str, Any] | None:
    state: dict[str, Any] = {}
    if items:
        state["responses_output_items"] = items
    if reasoning:
        state["reasoning_content"] = reasoning
    if blocks:
        state["thinking_blocks"] = blocks
    return normalize_provider_response_state(state)


@dataclass
class _Round:
    """One pi round as it streams to the reader."""

    call_id: str
    trace_meta: dict[str, Any]
    chunk_meta: dict[str, Any]
    defer: bool
    meter: CallMeasurement
    drafts: AskUserDraftEmitter | None
    started: float = field(default_factory=monotonic)
    last_progress: float = field(default_factory=monotonic)
    text: list[str] = field(default_factory=list)
    visible: list[str] = field(default_factory=list)
    reasoning: list[str] = field(default_factory=list)
    tools: ToolCallAccumulator = field(default_factory=ToolCallAccumulator)
    think: InlineThinkFilter = field(default_factory=InlineThinkFilter)
    dsml: DSMLStreamFilter = field(default_factory=DSMLStreamFilter)
    usage: Any = None
    finish_reason: str = ""
    output_chars: int = 0
    reasoning_chars: int = 0
    content_chars: int = 0
    tool_call_chars: int = 0
    action_started: bool = False
    output_emitted: bool = False
    completion_meta: dict[str, Any] | None = None
    deferred: bool = False


class PiTurn:
    """Run one chat turn on pi; answer loop.ts's asks with this pipeline's hooks."""

    def __init__(
        self,
        *,
        pipeline: "AgenticLoopPipeline",
        context: UnifiedContext,
        stream: StreamBus,
        enabled_tools: list[str],
        tool_schemas: list[dict[str, Any]] | None,
    ) -> None:
        self.pipeline = pipeline
        self.context = context
        self.stream = stream
        self.enabled_tools = enabled_tools
        self.tool_schemas = tool_schemas
        self.source = pipeline.event_source
        self.stage = pipeline.event_stage
        self.sources: list[dict[str, Any]] = []
        self._round: _Round | None = None
        self._last_request: LLMRequestSnapshot | None = None
        self._request_fingerprint = (context.runtime.previous_model_turn or {}).get("request_fingerprint")
        # Retrieved pixels belong to this turn's requests, never to durable history.
        self._transient_model_messages: list[dict[str, Any]] = []
        context.extension("source_visual_evidence")["image_hashes"] = []

    # ---- the turn ------------------------------------------------------

    async def run(self) -> dict[str, Any]:
        briefing = await self.pipeline._capability_pre_loop_briefings(self.context, self.stream)
        async with self.stream.stage(self.stage, source=self.source):
            seed = await self.pipeline._retrieve_kb_seed_block(self.context, self.stream)
            seed = "\n\n".join(
                b for b in (seed.strip(), self.pipeline._capability_pre_loop_seed(self.context).strip(), briefing.strip()) if b
            )
            messages = self.pipeline._build_loop_messages(
                context=self.context,
                enabled_tools=self.enabled_tools,
                kb_seed=seed,
                include_tool_manifest=bool(self.tool_schemas),
            )
            result: dict[str, Any] = {}
            try:
                request = {
                    "model": self.pipeline.model,
                    "messages": messages,
                    "tools": self.tool_schemas or None,
                    **self.pipeline._completion_kwargs(max_tokens=self.pipeline.loop_max_tokens),
                }
                cfg = {
                    # a connected provider that goes silent must settle; tests shorten it
                    **({"idle_timeout_s": float(os.environ["PI_TUTOR_IDLE_TIMEOUT_S"])} if os.environ.get("PI_TUTOR_IDLE_TIMEOUT_S") else {}),
                    "budget": max(1, self.pipeline.effective_max_rounds(self.context)),
                    # first round must call this tool (Ask Questions forces ask_user)
                    "initial_tool_choice": self.pipeline.initial_tool_choice,
                    "texts": {
                        "settle": self.pipeline._settle_exhausted_instruction(),
                        "finish_exhausted": self.pipeline._finish_exhausted_instruction(),
                        **{k: self.pipeline._t(f"loop.{k}", default=v) for k, v in _TEXTS.items()},
                    },
                }
                result = await pi_bridge.loop_turn(request, cfg, self)
                messages = result.get("messages") or messages
                self.tool_schemas = result.get("tools") or self.tool_schemas
                outcome = await self._outcome(result)
                if outcome.final_text and (messages[-1].get("role") != "assistant" or not messages[-1].get("content")):
                    messages.append({"role": "assistant", "content": outcome.final_text})
            finally:
                from deeptutor.services.session.model_history import (
                    complete_tool_results,
                    normalize_model_turn,
                )

                self.context.runtime.model_turn = normalize_model_turn(
                    {
                        "version": 1,
                        "messages": complete_tool_results(messages[self.pipeline._model_turn_start :]),
                        "system": messages[0]["content"],
                        "tools": self.tool_schemas,
                        "route": {"provider": self.pipeline.binding, "model": self.pipeline.model},
                        "request_fingerprint": self._request_fingerprint,
                    }
                )
        if outcome.provider_response_state is not None:
            self.context.runtime.provider_response_state = outcome.provider_response_state
        if self.sources:
            await self.stream.sources(self.sources, source=self.source, stage=self.stage, metadata={"trace_kind": "sources"})
        payload: dict[str, Any] = {
            "response": outcome.final_text,
            "completed": outcome.completed,
            "engine": "pi",
            "rounds": result.get("rounds", 0),
            "settlement_rounds": result.get("settlement_rounds", 0),
            "tool_steps": result.get("tool_steps", 0),
        }
        if self._last_request is not None:
            budget = self.pipeline.measure_context_budget(self._last_request)
            if budget is not None:
                payload["metadata"] = {"context_budget": budget}
        if self.pipeline.emit_result:
            await emit_capability_result(self.stream, payload, source=self.source, usage=self.pipeline.usage)
        return payload

    async def _outcome(self, r: dict[str, Any]) -> LoopOutcome:
        kind = r.get("error_kind")
        if kind == "reasoning_budget":
            raise LLMReasoningBudgetExhausted(
                self.pipeline._t(
                    "notices.reasoning_budget_exhausted",
                    default=(
                        "The model exhausted its output budget on internal reasoning without producing an answer "
                        "or tool action. Please retry, lower reasoning effort, or split the task into smaller steps."
                    ),
                ),
                diagnostics=r.get("diagnostics") or {},
            )
        if kind == "transport":
            partial = bool(r.get("partial"))
            logger.warning("pi agent turn failed: %s", r.get("error"))
            message = self.pipeline._t(
                "notices.provider_stream_interrupted" if partial else "notices.provider_unavailable",
                default=(
                    "The model provider interrupted this response. Please retry."
                    if partial
                    else "Unable to reach the model provider. Please retry."
                ),
            )
            if not partial and (hint := unreachable_endpoint_hint(self.pipeline.base_url)):
                message = f"{message} {hint}"
            raise LLMProviderTransportError(message, partial_response=partial)
        parts = list(r.get("parts") or [])
        if not r.get("completed"):
            return LoopOutcome("", False)
        if "terminal" in r:
            return LoopOutcome("".join([*parts, r.get("terminal") or ""]), True)
        cleaned = self._clean(r.get("raw") or "")
        visible = r.get("visible")
        final_text = "".join([*parts, visible if visible is not None else cleaned]) if parts else cleaned
        if not final_text and not r.get("allow_empty"):
            # the finish produced no usable text and nothing streamed: say so instead of an empty answer
            final_text = self.pipeline._t(
                "notices.reasoning_only_final_response" if r.get("empty_kind") == "reasoning_only" else "notices.empty_final_response",
                default=(
                    "The model produced internal reasoning but no usable answer. Please try again or narrow the request."
                    if r.get("empty_kind") == "reasoning_only"
                    else "I could not produce a useful response from the model output. Please try again or narrow the request."
                ),
            )
            await self.pipeline._emit_protocol_fallback_final_response(self.stream, final_text)
        return LoopOutcome(final_text, True, r.get("provider_state"))

    def _clean(self, text: str) -> str:
        return clean_thinking_tags(text, self.pipeline.binding, self.pipeline.model).strip()

    # ---- loop.ts asks ----------------------------------------------------

    async def ask(self, kind: str, data: dict[str, Any]) -> dict[str, Any]:
        if kind == "round_start":
            return await self._round_start(data)
        if kind == "round":
            return await self._round_end(data.get("error"))
        if kind == "finish_check":
            return await self._finish_check(data.get("text") or "")
        if kind == "dispatch":
            return await self._dispatch(data)
        if kind == "settlement_recovery":
            # A capability may ask for one restricted, receipt-backed tool round after settlement.
            recovery = self.pipeline._capability_settlement_recovery(self.context)
            if recovery is None:
                return {}
            instruction, names = recovery
            return {"instruction": instruction, "names": list(names)}
        if kind == "reject_finish":
            await self.discard()  # the rejected round's prose leaves the answer
            return {}
        raise ValueError(f"unknown pi ask: {kind}")

    async def notice(self, key: str, vars: dict[str, Any]) -> None:
        await self._progress(self.pipeline._t(f"notices.{key}", default=_NOTICES.get(key, key).format(**vars), **vars))

    async def release(self) -> None:
        """Publish a buffered capability round once its protocol accepted it."""
        r = self._round
        if r is None or not r.deferred:
            return
        visible = "".join(r.visible)
        if visible:
            await self.stream.content(visible, source=self.source, stage=self.stage, metadata=r.chunk_meta)
        if r.completion_meta is not None:
            await self.stream.progress("", source=self.source, stage=self.stage, metadata=r.completion_meta)
        r.deferred = False

    async def discard(self) -> None:
        """Take a rejected round's prose back out of the answer (same call_id, answer_visible False)."""
        r = self._round
        if r is None or r.completion_meta is None:
            return
        meta = {**r.completion_meta, "call_role": "narration", "answer_visible": False, "finish_rejected": True}
        await self.stream.progress("", source=self.source, stage=self.stage, metadata=meta)
        r.deferred = False
        r.completion_meta = None

    async def _progress(self, text: str, meta: dict[str, Any] | None = None) -> None:
        await self.stream.progress(text, source=self.source, stage=self.stage, metadata=meta or {"trace_kind": "warning"})

    # ---- rendering one round ---------------------------------------------

    async def _round_start(self, data: dict[str, Any]) -> dict[str, Any]:
        messages = data.get("messages") or []
        before = json.dumps(messages, sort_keys=True, default=str)
        forced = data.get("mode") == "forced"
        tools = None if forced else self.tool_schemas
        # The model-request boundary: refresh capability state, then add what only this request may carry.
        prepare = getattr(self.pipeline, "prepare_model_request", None)
        lesson_messages = await prepare(self.context, self.stream, messages) if prepare else []
        request_messages = with_transient_model_messages(messages, self._transient_model_messages)
        # Current tool pixels take precedence over cached lesson pages; neither belongs in the dialogue history.
        if lesson_messages and not has_image_parts(self._transient_model_messages):
            request_messages = [*request_messages, *lesson_messages]
        project = getattr(self.pipeline, "project_model_request", None)
        if project is not None:
            request_messages = project(request_messages)
        projected = deduplicate_user_images(
            [
                {key: value for key, value in message.items() if key not in {"_context_snapshot", "_lesson_packet"}}
                for message in request_messages
            ]
        )
        await self.pipeline._guard_context_window(projected, self.stream, tool_schemas=tools)
        self.context.extension("source_visual_evidence")["image_hashes"] = image_content_hashes(projected)
        settling = data.get("mode") in ("settle", "forced")
        label = self.pipeline._t("labels.final_response", default="Final response") if settling else self.pipeline._t(
            "labels.exploring", default="Exploring"
        )
        call_id = new_call_id(f"{self.source}-{self.stage}")
        # Runtime-only invocation identity: reviewed mutations can require a later model call.
        self.context.extension("agent_loop")["model_call_id"] = call_id
        from deeptutor.services.llm.request_cache import compare_requests, fingerprint_request

        fingerprint = fingerprint_request(
            projected,
            tools,
            {
                "provider": self.pipeline.binding,
                "model": self.pipeline.model,
                "base_url": getattr(self.pipeline.llm_config, "base_url", None),
                "wire_api": getattr(self.pipeline.llm_config, "wire_api", None),
            },
        )
        request_cache = compare_requests(self._request_fingerprint, fingerprint)
        self._request_fingerprint = fingerprint
        trace_meta = build_trace_metadata(
            call_id=call_id,
            phase=self.stage,
            label=label,
            call_kind="llm_final_response" if forced else "agent_loop_round",
            trace_id=call_id,
            trace_role="response" if settling else "explore",
            trace_group="stage",
        )
        trace_meta = merge_trace_metadata(trace_meta, {"request_cache": request_cache})
        carried = list(tools or []) or (self._last_request.tool_schemas if self._last_request else [])
        self._last_request = LLMRequestSnapshot(messages=list(projected), tool_schemas=carried)
        await self.stream.progress(
            label,
            source=self.source,
            stage=self.stage,
            metadata=merge_trace_metadata(trace_meta, {"trace_kind": "call_status", "call_state": "running"}),
        )
        defer = (not forced) and self.pipeline._capability_buffers_visible_output(self.context)
        token = measurement_active.set(False)
        try:
            meter = CallMeasurement(model=self.pipeline.model or "", provider=self.pipeline.binding, messages=projected)
        finally:
            measurement_active.reset(token)
        self._round = _Round(
            call_id=call_id,
            trace_meta=trace_meta,
            chunk_meta=merge_trace_metadata(trace_meta, {"trace_kind": "llm_chunk"}),
            defer=defer,
            meter=meter,
            drafts=None
            if defer
            else AskUserDraftEmitter(stream=self.stream, source=self.source, stage=self.stage, metadata=trace_meta),
        )
        self._round_messages = projected
        self._round_max_tokens = int(data.get("max_tokens") or self.pipeline.loop_max_tokens)
        reply: dict[str, Any] = {}
        if json.dumps(messages, sort_keys=True, default=str) != before:
            reply["messages"] = messages
        if projected != messages:  # only then does the request differ from the transcript (and cost a second copy)
            reply["request_messages"] = projected
        return reply

    async def _thinking(self, r: _Round, text: str) -> None:
        r.reasoning_chars += len(text)
        r.output_emitted = True
        await self.stream.thinking(text, source=self.source, stage=self.stage, metadata=r.chunk_meta)
        now = monotonic()
        if not r.action_started and now - r.last_progress >= 15.0:
            r.last_progress = now
            await self.stream.progress(
                self.pipeline._t(
                    "notices.reasoning_progress",
                    default="The model is still reasoning; it has not produced an answer or tool action yet.",
                ),
                source=self.source,
                stage=self.stage,
                metadata=merge_trace_metadata(
                    r.trace_meta,
                    {"trace_kind": "reasoning_progress", "reasoning_chars": r.reasoning_chars, "elapsed_s": round(now - r.started, 1)},
                ),
            )

    async def _segments(self, r: _Round, segments: list[tuple[str, str]]) -> None:
        for kind, segment in segments:
            if kind == "thinking":
                await self._thinking(r, segment)
                continue
            if segment.strip():
                r.action_started = True
                r.content_chars += len(segment)
            r.output_emitted = True
            r.visible.append(segment)
            if not r.defer:
                await self.stream.content(segment, source=self.source, stage=self.stage, metadata=r.chunk_meta)

    async def chunk(self, raw: dict[str, Any]) -> None:
        r = self._round
        if r is None:
            return
        chunk = ChatCompletionChunk.model_validate(raw)
        token = measurement_active.set(True)
        try:
            r.meter.chunk(chunk)
        finally:
            measurement_active.reset(token)
        if chunk.usage is not None:
            r.usage = chunk.usage
        if not chunk.choices:
            return
        choice = chunk.choices[0]
        if choice.finish_reason:
            r.finish_reason = str(choice.finish_reason)
        delta = choice.delta
        reasoning = getattr(delta, "reasoning_content", None) or getattr(delta, "reasoning", None)
        if reasoning:
            r.reasoning.append(reasoning)
            r.output_chars += len(reasoning)
            await self._thinking(r, reasoning)
        if delta.content:
            r.output_chars += len(delta.content)
            r.text.append(delta.content)
            visible = r.dsml.feed(delta.content)  # DSML markup never reaches the reader
            if visible:
                await self._segments(r, r.think.feed(visible))
        for tc in delta.tool_calls or []:
            fed = r.tools.feed(tc)
            r.tool_call_chars += fed
            r.output_chars += fed
            if fed:
                r.action_started = True
            if r.drafts is not None and (part := r.tools.part_at(int(tc.index or 0))) is not None:
                await r.drafts.observe(
                    call_id=str(part.get("id") or f"call_{tc.index}"),
                    tool_name=str(part.get("name") or ""),
                    arguments=str(part.get("arguments") or ""),
                )

    async def _round_end(self, error: str | None) -> dict[str, Any]:
        r = self._round
        assert r is not None
        tail = r.dsml.flush()
        if tail:
            await self._segments(r, r.think.feed(tail))
        await self._segments(r, r.think.flush())
        token = measurement_active.set(True)
        try:
            r.meter.finish(status="failed" if error else "completed")
        finally:
            measurement_active.reset(token)
        text = "".join(r.text)
        reasoning = "".join(r.reasoning)
        usage = usage_breakdown(r.usage)
        if error:
            await self.stream.progress(
                "",
                source=self.source,
                stage=self.stage,
                metadata=merge_trace_metadata(
                    r.trace_meta,
                    {
                        "trace_kind": "call_status",
                        "call_state": "failed",
                        "error_code": "provider_transport",
                        "retryable": True,
                        "partial_response": r.output_emitted,
                    },
                ),
            )
            return {"error": error, "output_emitted": r.output_emitted}
        record_streamed_usage(
            self.pipeline.usage,
            r.usage,
            input_chars=sum(message_content_chars(m) for m in self._round_messages),
            output_chars=r.output_chars,
        )
        tool_calls = r.tools.collected()
        if r.drafts is not None:
            await r.drafts.settle(tool_calls)
        # DeepSeek without native function calling writes its calls as DSML in the content channel
        dsml_calls, cleaned = extract_dsml_tool_calls(text, self.tool_schemas)
        if dsml_calls:
            tool_calls = tool_calls or dsml_calls
            text = cleaned
        visible = "".join(r.visible)
        truncated = finish_was_truncated(r.finish_reason)
        has_reasoning = bool(r.reasoning_chars or reasoning)
        reasoning_exhausted = bool(
            not tool_calls
            and not visible.strip()
            and has_reasoning
            and r.finish_reason.strip().lower() != "content_filter"
            and (truncated or usage.get("completion_tokens", 0) >= max(1, self._round_max_tokens))
        )
        meta: dict[str, Any] = {
            "trace_kind": "call_status",
            "call_state": "complete",
            # call_role is the round's phase (narration = more work follows); answer_visible says the text is answer
            "call_role": "narration" if tool_calls or truncated else "finish",
            "answer_visible": True,
            "finish_reason": r.finish_reason or "stop",
            "requested_max_tokens": self._round_max_tokens,
            "usage_reported": bool(usage),
            "output_chars": r.output_chars,
            "reasoning_chars": r.reasoning_chars,
            "content_chars": r.content_chars,
            "tool_call_chars": r.tool_call_chars,
            "tool_call_count": len(tool_calls),
        }
        for key in ("prompt_tokens", "completion_tokens", "total_tokens", "reasoning_tokens"):
            meta[key] = usage.get(key, "unavailable")
        if reasoning_exhausted:
            meta["reasoning_budget_exhausted"] = True
        r.completion_meta = merge_trace_metadata(r.trace_meta, meta)
        if r.defer:
            r.deferred = True
        else:
            await self.stream.progress("", source=self.source, stage=self.stage, metadata=r.completion_meta)
        state = _provider_state([], reasoning, [])
        if tool_calls:
            assistant = assistant_message_with_tool_calls(text, tool_calls, reasoning_content=reasoning or None)
        else:
            assistant = {"role": "assistant", "content": text}
            if reasoning:
                assistant["reasoning_content"] = reasoning
        if state is not None:
            assistant["_provider_response_state"] = state
        clean = self._clean(text)
        return {
            "text": text,
            "clean": clean,
            "visible": visible,
            "tool_calls": tool_calls,
            "finish_reason": r.finish_reason,
            "truncated": truncated,
            "reasoning_exhausted": reasoning_exhausted,
            "reasoning_without_answer": bool(reasoning) or bool(text.strip() and not clean),
            "replayable": bool(text or reasoning),
            "assistant": assistant,
            "provider_state": state,
            "output_emitted": r.output_emitted,
            "reasoning_chars": r.reasoning_chars,
            "completion_tokens": usage.get("completion_tokens"),
            "reasoning_tokens": usage.get("reasoning_tokens"),
        }

    # ---- capability hooks and tools ----------------------------------------

    async def _finish_check(self, text: str) -> dict[str, Any]:
        if not text:  # loop.ts probing before a tool-less salvage round
            return {"redirect": self.pipeline._capability_finish_instruction(self.context, "") or ""}
        redirect = self.pipeline._capability_finish_instruction(self.context, text)
        if redirect:
            await self.discard()
            return {"redirect": redirect}
        override = self.pipeline._capability_final_text_override(self.context, text)
        if override is not None:
            await self.discard()
            await self._publish_override(override)
            return {"override": override}
        await self.release()
        return {}

    async def _publish_override(self, text: str) -> None:
        if not self.context.capability_output.answer_published:
            await self.pipeline._emit_protocol_fallback_final_response(self.stream, text)
            self.context.capability_output.answer_published = True

    async def _dispatch(self, data: dict[str, Any]) -> dict[str, Any]:
        tool_calls = data.get("tool_calls") or []
        if data.get("truncated"):
            await self.notice("tool_call_truncated", {})
        names = tuple(str(c.get("name") or "") for c in tool_calls)
        if self.pipeline._capability_tool_round_output_policy(self.context, data.get("clean") or "", names) == "discard":
            await self.discard()
        else:
            await self.release()
        dispatch = await self.pipeline._dispatch_tool_calls(
            tool_calls=tool_calls,
            context=self.context,
            stream=self.stream,
            iteration_index=int(data.get("step") or 0),
            stage=self.stage,
        )
        self.sources.extend(dispatch.sources)
        extend_transient_model_messages(self._transient_model_messages, dispatch.model_messages)
        reply: dict[str, Any] = {"tool_messages": dispatch.tool_messages, "tools": self.tool_schemas}
        if dispatch.pause:
            reply["pause"] = True
            # the user's answers replace the matching role=tool message in place
            reply["resumed"] = await self.pipeline._await_user_reply_and_resolve(
                context=self.context, stream=self.stream, dispatch=dispatch
            )
            return reply
        for message in dispatch.tool_messages:
            meta = dispatch.tool_metadata_by_id.get(str(message.get("tool_call_id") or "")) or {}
            checkpoint = meta.get("_context_checkpoint")
            if isinstance(checkpoint, dict) and str(checkpoint.get("summary") or "").strip():
                reply["checkpoint"] = str(checkpoint["summary"]).strip()
                reply["checkpoint_state"] = str(checkpoint.get("state") or "").strip()
        override = self.pipeline._capability_final_text_override(self.context, "")
        if override is not None:
            await self._publish_override(override)
            reply["override"] = override
        elif dispatch.terminate:
            payload = dispatch.terminate_payload or {}
            await self.pipeline._emit_terminator_final_response(self.stream, payload)
            reply["terminate"] = True
            reply["terminate_text"] = str(payload.get("content") or "")
        return reply


__all__ = ["LOOP_STAGE", "LoopOutcome", "PiTurn"]
