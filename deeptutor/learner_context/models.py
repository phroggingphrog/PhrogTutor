"""Versioned, harness-neutral learner context data contracts."""

from __future__ import annotations

from typing import Literal, TypedDict

STYLE_LABELS = (
    "tried_first",
    "asked_for_answer",
    "pushed_back",
    "accepted",
    "own_idea",
)
StyleLabel = Literal["tried_first", "asked_for_answer", "pushed_back", "accepted", "own_idea"]


class StyleRecord(TypedDict):
    id: int
    ts: float
    style: StyleLabel
    quote: str
    source: str
    event_id: str


class LearnerStyleProfile(TypedDict):
    schema_version: int
    enabled: bool
    signals: int
    shares: dict[str, float]
    dominant: StyleLabel | None
    instruction: str
    records: list[StyleRecord]


class InteractionStyle(TypedDict):
    schema_version: int
    event_id: str
    source: str
    style: StyleLabel
    quote: str
    ts: float


class IntegrationCredential(TypedDict):
    id: str
    access_token: str
    name: str
    scopes: str
    created_at: str
    expires_at: str
