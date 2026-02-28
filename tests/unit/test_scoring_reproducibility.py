import pytest

from app.domain.scoring import CriteriaScore, deterministic_score_1_10


@pytest.mark.unit
def test_deterministic_score_reproducibility_for_same_inputs() -> None:
    criteria = [
        CriteriaScore(name="correctness", score=8, weight=0.35),
        CriteriaScore(name="completeness", score=7, weight=0.25),
        CriteriaScore(name="quality", score=8, weight=0.20),
        CriteriaScore(name="edges", score=7, weight=0.20),
    ]
    first = deterministic_score_1_10(criteria=criteria)
    second = deterministic_score_1_10(criteria=criteria)
    assert first == second == 8
