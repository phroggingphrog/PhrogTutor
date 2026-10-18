"""Unit tests for task complexity routing classifier."""

from __future__ import annotations

import pytest

from learning_tracking.academia_learning.routing import (
    VERDICT_COMPLEX,
    VERDICT_INSUFFICIENT,
    VERDICT_OUT_OF_SCOPE,
    VERDICT_SIMPLE,
    classify_task_complexity,
)


@pytest.mark.parametrize(
    "task_text",
    [
        "Fix typo in models.py docstring",
        "Fix spelling error in variable name",
        "Format code according to PEP8 indentation rules",
        "Remove trailing whitespace and format code",
        "Rename variable old_name to new_name",
        "Add type annotation to return value of get_user",
        "Add docstring explaining parameters for function calculate_metrics",
        "Bump version from 0.1.0 to 0.1.1 in pyproject.toml",
        "Single-step fix for off-by-one error",
        "Perform mechanical edit to replace deprecated function call",
        "Remove unused import from canvas.py",
        "Change constant value MAX_RETRIES to 5",
    ],
)
def test_classify_simple_tasks(task_text: str):
    verdict = classify_task_complexity(task_text)
    assert verdict == VERDICT_SIMPLE


@pytest.mark.parametrize(
    "task_text",
    [
        "Design architectural design for the distributed learning event bus",
        "Perform multi-component refactoring across storage, API, and pipeline modules",
        "Implement course concept integration connecting Canvas topics to objective hierarchy",
        "Develop an adaptive study strategy using spaced retrieval algorithms",
        "Plan curriculum planning and knowledge graph indexing for categorical analysis",
        "Execute database schema migration for versioned learning event hierarchy",
        "Design end-to-end integration pipeline with distributed consensus",
        "Redesign system architecture to decouple database connections from API handlers",
    ],
)
def test_classify_complex_tasks(task_text: str):
    verdict = classify_task_complexity(task_text)
    assert verdict == VERDICT_COMPLEX


@pytest.mark.parametrize(
    "task_text",
    [
        "",
        "   ",
        "fix it",
        "help",
        "do this",
        "task",
        "update",
        "check",
        "do something",
        "fix bug",
        "please help",
        "homework",
        "what should I do?",
    ],
)
def test_classify_insufficient_evidence_tasks(task_text: str):
    verdict = classify_task_complexity(task_text)
    assert verdict == VERDICT_INSUFFICIENT


@pytest.mark.parametrize(
    "task_text",
    [
        "What is the medical advice or treatment for high blood pressure symptoms?",
        "Diagnose my rash and recommend a prescription dosage",
        "I want to sue my landlord, need legal advice for a lawsuit",
        "Hire a divorce lawyer to draft a settlement agreement",
        "Fix my plumbing leak under the kitchen sink",
        "My car engine is making a rattling noise, how do I change brake pads?",
        "Repair microwave turntable motor",
        "Hello! How are you today?",
        "Tell me a joke.",
        "Good morning",
        "Which crypto trading advice or stock picks should I invest all my money in?",
    ],
)
def test_classify_out_of_scope_tasks(task_text: str):
    verdict = classify_task_complexity(task_text)
    assert verdict == VERDICT_OUT_OF_SCOPE


def test_classify_context_overrides():
    assert classify_task_complexity("arbitrary string", context={"complexity": "COMPLEX"}) == "COMPLEX"
    assert classify_task_complexity("arbitrary string", context={"complexity": "SIMPLE"}) == "SIMPLE"
    assert (
        classify_task_complexity("Fix typo in docstring", context={"missing_context": True})
        == VERDICT_INSUFFICIENT
    )
    assert (
        classify_task_complexity("Fix typo in docstring", context={"insufficient_evidence": True})
        == VERDICT_INSUFFICIENT
    )
