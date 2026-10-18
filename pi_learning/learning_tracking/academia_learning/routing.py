"""Task complexity routing classifier for academia learning.

Classifies incoming tasks into four distinct complexity tiers:
- SIMPLE: direct single-step fix, typo, formatting, mechanical edit.
- COMPLEX: architectural design, multi-component refactoring, course concept integration, study strategy.
- INSUFFICIENT_EVIDENCE: vague, ambiguous, or missing essential context.
- OUT_OF_SCOPE: unrelated tasks such as medical/legal advice, hardware configuration, random chit-chat.
"""

from __future__ import annotations

import re
from typing import Any, Dict, Optional


VERDICT_SIMPLE = "SIMPLE"
VERDICT_COMPLEX = "COMPLEX"
VERDICT_INSUFFICIENT = "INSUFFICIENT_EVIDENCE"
VERDICT_OUT_OF_SCOPE = "OUT_OF_SCOPE"

VALID_VERDICTS = {
    VERDICT_SIMPLE,
    VERDICT_COMPLEX,
    VERDICT_INSUFFICIENT,
    VERDICT_OUT_OF_SCOPE,
}

_OUT_OF_SCOPE_PATTERNS = [
    # Medical advice / treatment / diagnosis
    r"\b(?:medical\s+advice|diagnos(?:e|is)|prescrib(?:e|tion)|dosage|symptoms?|cure\s+cancer|treatment\s+for)\b",
    # Legal advice / lawsuits
    r"\b(?:legal\s+advice|lawsuit|sue\s+my|attorney|divorce\s+lawyer|draft\s+a\s+contract)\b",
    # Hardware / appliance / physical trades
    r"\b(?:plumbing|fix\s+my\s+sink|repair\s+microwave|car\s+engine|change\s+oil|brake\s+pads|solder\s+gpu|solder\s+motherboard|hvac)\b",
    # Casual chit-chat
    r"\b(?:how\s+are\s+you|what['’]?s\s+your\s+name|tell\s+me\s+a\s+joke|who\s+are\s+you|good\s+(?:morning|afternoon|evening))\b",
    r"^(?:hello|hi|hey|greetings)\b",
    # Financial speculation / investing
    r"\b(?:stock\s+picks?|crypto\s+trading\s+advice|invest\s+(?:all\s+)?my\s+money|buy\s+bitcoin)\b",
]

_COMPLEX_PATTERNS = [
    r"\b(?:architectural\s+design|system\s+architecture|redesign\s+system)\b",
    r"\b(?:multi-component\s+refactoring|multi-component|system-wide\s+refactoring)\b",
    r"\b(?:course\s+concept\s+integration|curriculum\s+planning|study\s+strategy)\b",
    r"\b(?:spaced\s+retrieval\s+algorithm|knowledge\s+graph\s+indexing|learning\s+cascade)\b",
    r"\b(?:distributed\s+(?:consensus|system)|schema\s+migration|end-to-end\s+integration)\b",
    r"\b(?:refactor|redesign|architect)\b.*\b(?:architecture|pipeline|framework|infrastructure)\b",
]

_SIMPLE_PATTERNS = [
    r"\b(?:typo|fix\s+typo|spelling\s+error|fix\s+spelling)\b",
    r"\b(?:formatting|format\s+code|pep8|fix\s+indentation|trailing\s+whitespace)\b",
    r"\b(?:rename\s+(?:variable|function|method|argument)|add\s+(?:docstring|comment|type\s+annotation))\b",
    r"\b(?:bump\s+version|update\s+version|single-step\s+fix|one-line\s+fix|mechanical\s+edit)\b",
    r"\b(?:remove\s+unused\s+import|fix\s+import|change\s+constant\s+value)\b",
]

_VAGUE_PHRASES = {
    "fix it",
    "help",
    "do this",
    "task",
    "update",
    "check",
    "run",
    "do something",
    "fix bug",
    "test",
    "please help",
    "implement feature",
    "do assignment",
    "homework",
    "fix",
    "look at this",
    "what should i do",
    "check code",
}


def classify_task_complexity(
    task_text: str,
    context: Optional[Dict[str, Any]] = None,
) -> str:
    """Classify task complexity into SIMPLE, COMPLEX, INSUFFICIENT_EVIDENCE, or OUT_OF_SCOPE."""
    if context:
        override = context.get("complexity") or context.get("verdict")
        if override in VALID_VERDICTS:
            return override
        if context.get("insufficient_evidence") or context.get("missing_context"):
            return VERDICT_INSUFFICIENT

    if not task_text or not isinstance(task_text, str):
        return VERDICT_INSUFFICIENT

    text = task_text.strip()
    if not text:
        return VERDICT_INSUFFICIENT

    lowered = text.lower()
    cleaned = re.sub(r"[^\w\s]", "", lowered).strip()

    # Check for explicitly vague short queries
    if cleaned in _VAGUE_PHRASES:
        return VERDICT_INSUFFICIENT

    # Check out-of-scope patterns first
    for pat in _OUT_OF_SCOPE_PATTERNS:
        if re.search(pat, text, re.IGNORECASE):
            return VERDICT_OUT_OF_SCOPE

    # Check complex patterns next
    for pat in _COMPLEX_PATTERNS:
        if re.search(pat, text, re.IGNORECASE):
            return VERDICT_COMPLEX

    # Check simple patterns
    for pat in _SIMPLE_PATTERNS:
        if re.search(pat, text, re.IGNORECASE):
            return VERDICT_SIMPLE

    # If the text is very short (< 4 words) and lacks concrete action nouns, it's insufficient evidence
    words = text.split()
    if len(words) < 4:
        # Check if any simple keyword matches despite shortness
        if any(re.search(pat, text, re.IGNORECASE) for pat in _SIMPLE_PATTERNS):
            return VERDICT_SIMPLE
        return VERDICT_INSUFFICIENT

    # Secondary heuristic for open-ended or complex descriptions
    if any(k in lowered for k in ("redesign", "architect", "pipeline", "strategy", "curriculum", "consensus")):
        return VERDICT_COMPLEX

    # Secondary heuristic for simple direct edits
    if any(k in lowered for k in ("typo", "spelling", "rename", "format", "docstring", "comment", "lint")):
        return VERDICT_SIMPLE

    # Default to INSUFFICIENT_EVIDENCE if unclear without context
    if len(words) < 6:
        return VERDICT_INSUFFICIENT

    # For descriptive tasks without explicit match, check if complex or simple phrasing
    if any(k in lowered for k in ("design", "system", "integrate", "infrastructure", "migrate")):
        return VERDICT_COMPLEX

    return VERDICT_SIMPLE
