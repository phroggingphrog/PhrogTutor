from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Optional


@dataclass(frozen=True)
class Assignment:
    id: str
    title: str
    course: str
    due_at: Optional[datetime] = None
    personal_final: Optional[datetime] = None
    prerequisites: tuple[str, ...] = ()
    progress: float = 0.0
    original_due_at: Optional[datetime] = None
    extended_due_at: Optional[datetime] = None
    protection_start: Optional[datetime] = None
