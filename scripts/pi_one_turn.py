"""Prove the pi turn engine: run ONE real turn through TurnEngine and print the StreamEvents.
Usage: PI_TUTOR_ENGINE=1 python scripts/pi_one_turn.py "question"
"""
import asyncio
import json
import sys

from deeptutor.core.context import UnifiedContext
from deeptutor.runtime.turn_engine import TurnEngine


async def main() -> None:
    ctx = UnifiedContext(user_message=" ".join(sys.argv[1:]) or "What is Bayes' theorem? Check my wiki notes.")
    async for ev in TurnEngine().execute(ctx):
        d = ev.to_dict()
        d["content"] = d["content"][:160]
        print(json.dumps({k: d[k] for k in ("type", "source", "content", "metadata")}, ensure_ascii=False))


asyncio.run(main())
