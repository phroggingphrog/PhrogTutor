"""Deterministic answer grading, ported as a pattern from DeepTutor
(`deeptutor/learning/grading.py`): fast, LLM-free scoring that runs BEFORE
any agent judgment, so a correct/incorrect verdict never depends on model
mood or prompt drift for the mechanical cases it can decide outright.

Three tiers, exactly as reported:
1. Exact match (after whitespace/case normalization) -- multiple-choice,
   short factual answers.
2. Fuzzy short-string match (`difflib.SequenceMatcher.ratio() >= 0.85`) --
   answers <=30 characters, typos/near-misses.
3. Keyword-density match (matched-keyword fraction >= 0.6) -- open-ended
   answers where the expected keywords must appear, in any order.

Anything this module cannot confidently decide returns
``GradeResult.verdict == "NEEDS_JUDGMENT"`` -- it is a first-pass filter,
not a replacement for the agent grading free-text explanation quality;
Academia still reviews NEEDS_JUDGMENT cases itself.

Error taxonomy (DeepTutor's four types) is a classification VOCABULARY
only here -- assigning the actual type for a given wrong answer is a
judgment call left to the caller (typically Academia reading the
mismatch), not computed by this module.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from difflib import SequenceMatcher

FUZZY_SHORT_MAX_CHARS = 30
FUZZY_RATIO_THRESHOLD = 0.85
KEYWORD_DENSITY_THRESHOLD = 0.6

ERROR_TAXONOMY = (
    "structural",  # knowledge missing entirely
    "deviation",  # misconception -- believed something specific but wrong
    "application",  # knew the concept, slipped executing it
    "metacognitive",  # blank, guessed, or explicitly unsure
)


@dataclass(frozen=True)
class GradeResult:
    verdict: str  # "CORRECT" | "INCORRECT" | "NEEDS_JUDGMENT"
    method: str  # "exact_match" | "fuzzy_short" | "keyword_density" | "needs_judgment"
    score: float | None  # 0..1 where applicable (ratio or density), None for exact/needs_judgment
    detail: str

    def to_dict(self) -> dict:
        return {
            "verdict": self.verdict,
            "method": self.method,
            "score": round(self.score, 4) if self.score is not None else None,
            "detail": self.detail,
        }


def _normalize(s: str) -> str:
    return re.sub(r"\s+", " ", s.strip().casefold())


def grade_answer(
    response: str,
    expected: str,
    *,
    keywords: list[str] | None = None,
) -> GradeResult:
    """Grade one free-text response against an expected answer.

    ``keywords`` (if supplied) triggers tier 3 for long/open-ended answers
    where exact or fuzzy match is not meaningful (e.g. a short-answer
    explanation expected to mention several specific terms). Without
    ``keywords``, a response longer than the fuzzy-short cutoff that
    doesn't exact-match falls through to NEEDS_JUDGMENT rather than being
    scored by a method that wasn't designed for it.
    """
    norm_response = _normalize(response)
    norm_expected = _normalize(expected)

    if norm_response == norm_expected:
        return GradeResult(
            verdict="CORRECT", method="exact_match", score=None, detail="exact normalized match"
        )

    if len(norm_expected) <= FUZZY_SHORT_MAX_CHARS and len(norm_response) <= FUZZY_SHORT_MAX_CHARS:
        ratio = SequenceMatcher(None, norm_response, norm_expected).ratio()
        if ratio >= FUZZY_RATIO_THRESHOLD:
            return GradeResult(
                verdict="CORRECT",
                method="fuzzy_short",
                score=ratio,
                detail=f"SequenceMatcher ratio {ratio:.3f} >= {FUZZY_RATIO_THRESHOLD}",
            )
        return GradeResult(
            verdict="INCORRECT",
            method="fuzzy_short",
            score=ratio,
            detail=f"SequenceMatcher ratio {ratio:.3f} < {FUZZY_RATIO_THRESHOLD}",
        )

    if keywords:
        response_terms = set(re.findall(r"[a-z0-9]+", norm_response))
        matched = sum(1 for kw in keywords if _normalize(kw) in norm_response or kw.casefold() in response_terms)
        density = matched / len(keywords)
        if density >= KEYWORD_DENSITY_THRESHOLD:
            return GradeResult(
                verdict="CORRECT",
                method="keyword_density",
                score=density,
                detail=f"{matched}/{len(keywords)} keywords matched ({density:.3f} >= {KEYWORD_DENSITY_THRESHOLD})",
            )
        return GradeResult(
            verdict="INCORRECT",
            method="keyword_density",
            score=density,
            detail=f"{matched}/{len(keywords)} keywords matched ({density:.3f} < {KEYWORD_DENSITY_THRESHOLD})",
        )

    return GradeResult(
        verdict="NEEDS_JUDGMENT",
        method="needs_judgment",
        score=None,
        detail="response too long for fuzzy match and no keyword list supplied; agent must grade this one",
    )
