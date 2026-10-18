"""Run each DeepTutor mode once on the pi SDK and print one line per mode: OK/FAIL, pi calls, seconds, a snippet.
Usage: PYTHONPATH=. DEEPTUTOR_WORKSPACE_ROOT=<dir> .venv/bin/python scripts/pi_modes_once.py [mode ...]
"""
import asyncio
import os
import sys
import time
import traceback

from deeptutor.services.llm import pi_bridge

CALLS = {"n": 0}
_orig = pi_bridge._chunks


TRACE = []


async def _counting(request):
    CALLS["n"] += 1
    t0, finish, tools, n_chars = time.time(), None, [], 0
    async for c in _orig(request):
        ch = (c.get("choices") or [{}])[0]
        d = ch.get("delta") or {}
        n_chars += len(d.get("content") or "")
        tools += [t["function"]["name"] for t in d.get("tool_calls") or []]
        finish = ch.get("finish_reason") or finish
        yield c
    TRACE.append(f"pi call {CALLS['n']}: {time.time() - t0:.1f}s finish={finish} tools={tools} text={n_chars} chars, roles={[m.get('role') for m in request.get('messages') or []]}")


pi_bridge._chunks = _counting

# pi agent turns: count each round loop.ts starts
from deeptutor.agents.loop.pi_turn import PiTurn  # noqa: E402

_orig_round = PiTurn._round_start


async def _round_counting(self, data):
    CALLS["n"] += 1
    TRACE.append(f"pi round {CALLS['n']}: mode={data.get('mode')}")
    return await _orig_round(self, data)


PiTurn._round_start = _round_counting
_orig_end = PiTurn._round_end


async def _end_tracing(self, error):
    out = await _orig_end(self, error)
    TRACE.append(f"  -> finish={out.get('finish_reason')} tools={[c['name'] for c in out.get('tool_calls') or []]} text={len(out.get('visible') or '')} chars error={error}")
    return out


PiTurn._round_end = _end_tracing

# vanilla: count DeepTutor's own OpenAI calls the same way
from openai.resources.chat.completions import AsyncCompletions  # noqa: E402

_orig_create = AsyncCompletions.create


async def _vanilla_create(self, *a, **kw):
    CALLS["n"] += 1
    n, t0 = CALLS["n"], time.time()
    resp = await _orig_create(self, *a, **kw)
    if not kw.get("stream"):
        TRACE.append(f"vanilla call {n}: {time.time() - t0:.1f}s")
        return resp

    async def timed():
        cached = None
        async for c in resp:
            u = getattr(c, "usage", None)
            if u is not None and getattr(u, "prompt_tokens_details", None) is not None:
                cached = f"{u.prompt_tokens_details.cached_tokens}/{u.prompt_tokens}"
            yield c
        TRACE.append(f"vanilla call {n}: {time.time() - t0:.1f}s cached={cached}")

    return timed()


AsyncCompletions.create = _vanilla_create

# RUN_MODEL=<OmniRoute model or combo>: use it instead of the catalog's active model, for this run only
if os.environ.get("RUN_MODEL"):
    from deeptutor.services.config.model_catalog import ModelCatalogService

    _orig_load = ModelCatalogService.load

    def _load(self, *a, **kw):
        cat = _orig_load(self, *a, **kw)
        llm = cat.get("services", {}).get("llm", {})
        for prof in llm.get("profiles", []):
            for m in prof.get("models", []):
                if m.get("id") == llm.get("active_model_id"):
                    m["model"] = os.environ["RUN_MODEL"]
        return cat

    ModelCatalogService.load = _load

TURN_MODES = {
    "solve": ("deep_solve", "Solve: a fair coin is tossed 3 times. What is P(at least 2 heads)?"),
    "research": ("deep_research", "Briefly research: what is a highest density interval? Keep it short.", {"mode": "notes", "depth": "quick"}),
    "question": ("deep_question", "Make one multiple-choice question about Bayes' theorem."),
    "ask": ("ask_questions", "Help me pick a statistics topic to revise."),
    "visualize": ("visualize", "Visualize a normal distribution with mean 0 and sd 1."),
}


async def turn_mode(cap: str, prompt: str, config: dict | None = None) -> str:
    from deeptutor.core.context import UnifiedContext
    from deeptutor.runtime.turn_engine import TurnEngine

    ctx = UnifiedContext(user_message=prompt, active_capability=cap, config_overrides=config or {})
    text, err = [], None
    async for ev in TurnEngine().execute(ctx):
        d = ev.to_dict()
        if d["type"] == "content":
            text.append(d["content"] or "")
        elif d["type"] == "result":
            text.append(str(d["metadata"].get("response") or d["metadata"])[:300])
        elif d["type"] == "error":
            err = d["content"]
        elif d["type"] == "done" and d["metadata"].get("status") != "completed":
            err = err or f"done status {d['metadata'].get('status')}"
    if err:
        raise RuntimeError(err)
    return "".join(text)


_CLIENT = None


def api_mode(name: str) -> str:
    global _CLIENT
    from fastapi.testclient import TestClient

    from deeptutor.api.main import app

    if _CLIENT is None:  # one app lifespan for all API modes: closing it shuts the turn coordinator
        _CLIENT = TestClient(app).__enter__()
    c = _CLIENT
    if True:
        if name == "co-writer":
            r = c.post("/api/documents/actions/edit", json={"text": "Bayes theorem is a rule for updating beliefs.", "instruction": "Make it clearer.", "action": "rewrite"})
        elif name == "book":
            r = c.post("/api/books", json={"user_intent": "A two-page intro to Bayes' theorem.", "depth": "quick"})
        else:  # partners
            r = c.post("/api/partners", json={"name": f"check-partner-{int(time.time())}", "description": "test partner for the pi port"})
            if r.status_code < 300:
                pid = r.json().get("partner_id") or r.json().get("id")
                r = c.post(f"/api/partners/{pid}/chat", json={"content": "Say hello in five words."})
        if r.status_code >= 300:
            raise RuntimeError(f"HTTP {r.status_code}: {r.text[:300]}")
        return r.text


async def main() -> None:
    modes = sys.argv[1:] or [*TURN_MODES, "co-writer", "book", "partners"]
    for m in modes:
        CALLS["n"], t0 = 0, time.time()
        try:
            out = await turn_mode(*TURN_MODES[m]) if m in TURN_MODES else await asyncio.to_thread(api_mode, m)
            status = "OK"
            snippet = " ".join(out.split())[:400]
        except Exception as exc:  # noqa: BLE001
            status, snippet = "FAIL", f"{type(exc).__name__}: {exc}"[:300]
            traceback.print_exc(file=sys.stderr)
        engine = "pi" if pi_bridge.pi_sdk_enabled() else "vanilla"
        if os.environ.get("TRACE_CALLS"):
            print("\n".join(TRACE), flush=True)
        TRACE.clear()
        print(f"RESULT {engine} {m}: {status} | model calls {CALLS['n']} | {time.time() - t0:.0f}s | {snippet}", flush=True)


asyncio.run(main())
