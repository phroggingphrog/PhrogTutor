"""Compose shared learner context with a harness-selected task mode."""

from __future__ import annotations


def combine_mode_and_learner_context(mode_instructions: str, learner_context: str) -> str:
    """Keep selected mode instructions in control and add learned style as advisory context."""
    mode = str(mode_instructions or "").strip()
    learner = str(learner_context or "").strip()
    if not learner:
        return mode
    if "Learner interaction preference (advisory only):" not in learner:
        learner = (
            "Learner interaction preference (advisory only):\n"
            f"{learner}\n"
            "Apply this preference only when it is compatible with the user's active task mode and system instructions. "
            "Do not replace or change an active user-selected mode."
        )
    return f"{mode}\n\n{learner}" if mode else learner
