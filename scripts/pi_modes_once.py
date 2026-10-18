"""Run each DeepTutor mode once on the pi SDK and print one line per mode: OK/FAIL, pi calls, seconds, a snippet.
Usage: PYTHONPATH=. DEEPTUTOR_WORKSPACE_ROOT=<dir> .venv/bin/python scripts/pi_modes_once.py [mode ...]
"""
import asyncio
import sys
import time
import traceback

from deeptutor.services.llm import pi_bridge

CALLS = {"n": 0}
_orig = pi_bridge._chunks


async def _counting(request):
    CALLS["n"] += 1
    async for c in _orig(request):
        yield c


pi_bridge._chunks = _counting

TURN_MODES = {
    "solve": ("deep_solve", "Solve: a fair coin is tossed 3 times. What is P(at least 2 heads)?"),
    "research": ("deep_research", "Briefly research: what is a highest density interval? Keep it short.", {"mode": "notes", "depth": "quick"}),
    "question": ("deep_question", "Make one multiple-choice question about Bayes' theorem."),
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
            r = c.post("/api/partners", json={"name": "pi-check-partner", "description": "test partner for the pi port"})
            if r.status_code < 300:
                pid = r.json().get("partner_id") or r.json().get("id") or "pi-check-partner"
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
            status = "OK" if CALLS["n"] else "OK-but-no-pi-call"
            snippet = " ".join(out.split())[:160]
        except Exception as exc:  # noqa: BLE001
            status, snippet = "FAIL", f"{type(exc).__name__}: {exc}"[:300]
            traceback.print_exc(file=sys.stderr)
        print(f"RESULT {m}: {status} | pi calls {CALLS['n']} | {time.time() - t0:.0f}s | {snippet}", flush=True)


asyncio.run(main())
