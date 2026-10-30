from __future__ import annotations

import asyncio

import pytest

from deeptutor.learner_context.prompt import combine_mode_and_learner_context
from deeptutor.services import chat_mastery


def test_mode_and_learner_context_are_composed_without_mode_override():
    result = combine_mode_and_learner_context(
        "Discussion mode: BRAINSTORM. Explore options before choosing.",
        "How this learner uses AI: Build on their own ideas first.",
    )
    assert result.index("Discussion mode: BRAINSTORM") < result.index("Learner interaction preference (advisory only)")
    assert "Do not replace or change an active user-selected mode." in result
    assert "Build on their own ideas first" in result


def test_empty_learner_context_leaves_mode_unchanged():
    mode = "Discussion mode: LEARNING."
    assert combine_mode_and_learner_context(mode, "") == mode


@pytest.mark.asyncio
async def test_selected_discussion_mode_coexists_with_learned_style(monkeypatch):
    import deeptutor.services.discussion_modes as discussion_modes

    monkeypatch.setattr(discussion_modes, "mode_block", lambda mode: "\n\nDiscussion mode: BRAINSTORM; generate and narrow options.")
    monkeypatch.setattr(chat_mastery, "_style_line", lambda: "\nHow this learner uses AI:\nBuild on their own ideas first.")
    monkeypatch.setattr(chat_mastery, "mastery", lambda: [])

    async def no_related(_message):
        return set()

    monkeypatch.setattr(chat_mastery, "_related", no_related)
    prompt = await chat_mastery.prompt_block("design a tutor", mode="brainstorm")
    assert "Discussion mode: BRAINSTORM" in prompt
    assert "Learner interaction preference (advisory only)" in prompt
    assert "Build on their own ideas first" in prompt
