from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass


@dataclass(frozen=True)
class CriteriaScore:
    name: str
    score: int
    weight: float


def deterministic_score_1_10(*, criteria: Sequence[CriteriaScore]) -> int:
    if not criteria:
        return 1

    weighted_sum = 0.0
    weights = 0.0
    for item in criteria:
        bounded_score = max(1, min(10, item.score))
        bounded_weight = max(0.0, item.weight)
        weighted_sum += bounded_score * bounded_weight
        weights += bounded_weight

    if weights == 0:
        return 1

    return int(round(weighted_sum / weights))
