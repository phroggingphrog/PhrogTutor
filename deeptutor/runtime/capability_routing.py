"""Pre-execution capability routing for explicit turn requests."""

from __future__ import annotations

from dataclasses import dataclass, field
import re
from typing import Any


@dataclass(frozen=True)
class CapabilityRoute:
    requested_capability: str
    capability: str
    confidence: float
    strategy: str
    reason: str
    # Options the request itself states (e.g. the number of questions); the routed
    # capability validates them against its own schema.
    config: dict[str, Any] = field(default_factory=dict)

    def as_metadata(self) -> dict[str, Any]:
        return {
            "requested_capability": self.requested_capability,
            "capability": self.capability,
            "confidence": self.confidence,
            "strategy": self.strategy,
            "reason": self.reason,
            "auto_routed": self.auto_routed,
        }

    @property
    def auto_routed(self) -> bool:
        return self.capability != self.requested_capability


# Deliberately narrow: an ambiguous mention of practice, a quiz, or an exam
# stays in chat. Only a request to generate a reusable question set routes to
# deep_question. Interactive requests such as "quiz me" stay in chat because
# deep_question is a static generator, not a turn-by-turn quiz runner.
_QUIZ_PATTERNS = (
    re.compile(
        r"\b(?:generate|create|make|write|give me|produce)\b[^.!?\n]{0,80}"
        r"\bquiz questions?\b",
        re.IGNORECASE,
    ),
    re.compile(r"(?:给我出|帮我出|生成|创建)[^.!?\n]{0,20}(?:测验|考试|测试)题?"),
)

_QUIZ_REASON = "The user explicitly asked to generate a quiz question set."

_NUMBER_WORDS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
}
_QUESTION_COUNT = re.compile(
    r"\b(\d{1,2}|" + "|".join(_NUMBER_WORDS) + r")\b(?:\s+[\w-]+){0,3}?\s+(?:quiz\s+)?questions?\b",
    re.IGNORECASE,
)


def _requested_question_count(message: str) -> int | None:
    """The question count the learner asked for ("3 quiz questions", "five questions"), if any."""
    match = _QUESTION_COUNT.search(message)
    if not match:
        return None
    raw = match.group(1).lower()
    count = _NUMBER_WORDS.get(raw) or (int(raw) if raw.isdigit() else 0)
    return count if 1 <= count <= 50 else None


def route_explicit_quiz_request(
    content: Any,
    requested_capability: Any,
    *,
    enabled: bool,
    workspace_mode: Any = "",
) -> CapabilityRoute | None:
    """Return a rule-based route before turn creation, or None to keep chat.

    The requested capability is retained even when routing does not occur so
    observability can distinguish an explicit chat selection from a missing one.

    A conversation held inside a workspace is never routed. A workspace is a
    standing commitment — a mastery path to advance, a document to stay in —
    and only some actions carry it: routing a mastery turn to the static quiz
    generator would drop the tutor loop, the objective map and the mastery
    gate, leaving the learner being taught from their materials while their
    path stands still. Learners in a workspace can still pick another action
    themselves; what they cannot do is have it picked for them.
    """
    requested = str(requested_capability or "chat")
    if not enabled or requested != "chat":
        return None
    if str(workspace_mode or "").strip():
        return None

    message = str(content or "").strip()
    if not message or not any(pattern.search(message) for pattern in _QUIZ_PATTERNS):
        return None

    return CapabilityRoute(
        requested_capability=requested,
        capability="deep_question",
        confidence=0.96,
        strategy="rule",
        reason=_QUIZ_REASON,
        config=({"num_questions": count} if (count := _requested_question_count(message)) else {}),
    )


__all__ = ["CapabilityRoute", "route_explicit_quiz_request"]
