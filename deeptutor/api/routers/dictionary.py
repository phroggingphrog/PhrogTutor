"""Coding dictionary: "Save to dictionary" on a chat code block.

One cheap model call names the topic and writes a one-line description; the entry is queued in
<data>/dictionary-drafts/<topic>.md and the outside filer appends it to wiki/cheatsheets/dictionary/
(DeepTutor never writes the home folder).
"""

from __future__ import annotations

import asyncio
import json
import re

from fastapi import APIRouter
from pydantic import BaseModel

from deeptutor.services.chat_mastery import _EXTRACT_MODEL
from deeptutor.services.path_service import get_path_service

router = APIRouter()


class Entry(BaseModel):
    code: str
    language: str = ""


@router.post("")
async def save(entry: Entry):
    from deeptutor.services.llm import complete

    raw = await asyncio.wait_for(
        complete(
            prompt=f"Language: {entry.language}\n```\n{entry.code[:3000]}\n```",
            system_prompt=(
                "This is a command or code snippet for a learner's personal coding dictionary. Reply with JSON only: "
                '{"topic": "<one lowercase word for the tool or language, e.g. git, sql, python, r, bash, docker>", '
                '"description": "<one plain sentence: what it does>"}'
            ),
            model=_EXTRACT_MODEL, temperature=0, max_tokens=150, reasoning_effort="none",
        ),
        timeout=60,
    )
    match = re.search(r"\{.*\}", raw or "", re.S)
    data = json.loads(match.group(0)) if match else {}
    topic = re.sub(r"[^a-z0-9-]+", "-", str(data.get("topic") or entry.language or "misc").lower()).strip("-") or "misc"
    desc = " ".join(str(data.get("description") or "").split())
    code = entry.code.strip()
    line = f"- `{code}` — {desc}\n" if "\n" not in code and "`" not in code else f"- {desc}\n\n```{entry.language}\n{code}\n```\n"
    d = get_path_service().user_data_dir / "dictionary-drafts"
    d.mkdir(parents=True, exist_ok=True)
    with (d / f"{topic}.md").open("a", encoding="utf-8") as f:
        f.write(line)
    return {"topic": topic, "description": desc}
