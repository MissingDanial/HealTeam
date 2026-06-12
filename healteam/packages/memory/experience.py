"""Experience-library helpers.

The experience library is the cross-session memory of "what works in therapy":
patterns of the form *(condition, hint, weight)*, where `weight` is updated by
the L1 evolution engine (the observer agent).

W1 ships pure-Python helpers only — the W2 `persistence` task will add
pgvector-backed semantic retrieval (`find_similar(condition, top_k)`).
"""

from __future__ import annotations

from typing import Iterable

from .schema import Experience, School


def filter_by_school(experiences: Iterable[Experience], school: School) -> list[Experience]:
    """Return only experiences for the given school, sorted by weight desc."""
    return sorted([e for e in experiences if e.school == school], key=lambda e: e.weight, reverse=True)


def update_weight(exp: Experience, delta: float, *, min_w: float = 0.0, max_w: float = 1.0) -> Experience:
    """Bump an experience's weight by `delta`, clamped to [min_w, max_w].

    Returns a new Experience; does not mutate the input.
    """
    new_w = max(min_w, min(max_w, exp.weight + delta))
    return exp.model_copy(update={"weight": new_w})


def matches_condition(exp: Experience, condition_keywords: list[str]) -> bool:
    """Cheap keyword match: does the experience's `condition` text contain any of the keywords?

    This is a W1 placeholder. The W2 persistence task replaces it with
    embedding-similarity search via pgvector.
    """
    cond_lc = exp.condition.lower()
    return any(kw.lower() in cond_lc for kw in condition_keywords)


__all__ = ["filter_by_school", "update_weight", "matches_condition"]
