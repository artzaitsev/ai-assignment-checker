from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

import pytest

from app.domain.evaluation_contracts import (
    CandidateFeedback,
    OrganizerFeedback,
    ScoreBreakdown,
    TaskScoreBreakdown,
    CriterionScore,
    parse_task_schema,
)
from app.domain.dto import EvaluateSubmissionCommand, LLMClientResult, PrepareExportCommand
from app.domain.evaluation_chain import load_chain_spec
from app.domain.models import SubmissionListItem
from app.domain.use_cases.deliver import prepare_export
from app.domain.use_cases.llm_eval import evaluate_submission
from app.lib.artifacts.types import NormalizedArtifact


def _valid_schema() -> dict[str, object]:
    return {
        "schema_version": "task-criteria:v1",
        "tasks": [
            {
                "task_id": "task_1",
                "title": "Task 1",
                "weight": 0.6,
                "criteria": [
                    {"criterion_id": "correctness", "description": "Correctness", "weight": 0.7},
                    {"criterion_id": "clarity", "description": "Clarity", "weight": 0.3},
                ],
            },
            {
                "task_id": "task_2",
                "title": "Task 2",
                "weight": 0.4,
                "criteria": [
                    {"criterion_id": "coverage", "description": "Coverage", "weight": 1.0},
                ],
            },
        ],
    }


@dataclass(frozen=True)
class _MultitaskLLM:
    def evaluate(self, request: object) -> LLMClientResult:
        del request
        return LLMClientResult(
            raw_text="",
            raw_json={
                "tasks": [
                    {
                        "task_id": "task_1",
                        "criteria": [
                            {"criterion_id": "correctness", "score": 8, "reason": "good"},
                            {"criterion_id": "clarity", "score": 7, "reason": "ok"},
                        ],
                    },
                    {
                        "task_id": "task_2",
                        "criteria": [
                            {"criterion_id": "coverage", "score": 9, "reason": "strong"},
                        ],
                    },
                ],
                "organizer_feedback": {"strengths": ["s"], "issues": ["i"], "recommendations": ["r"]},
                "candidate_feedback": {"summary": "sum", "what_went_well": ["w"], "what_to_improve": ["m"]},
                "ai_assistance": {"likelihood": 0.3, "confidence": 0.6, "disclaimer": "d"},
            },
            tokens_input=10,
            tokens_output=10,
            latency_ms=1,
        )


@pytest.mark.unit
def test_criteria_schema_validation_rejects_non_normalized_weights() -> None:
    invalid = _valid_schema()
    tasks = invalid["tasks"]
    assert isinstance(tasks, list)
    first_task = tasks[0]
    assert isinstance(first_task, dict)
    first_task["weight"] = 0.7
    with pytest.raises(ValueError, match="tasks weights"):
        parse_task_schema(invalid)


@pytest.mark.unit
def test_multitask_evaluate_produces_deterministic_task_and_overall_scores() -> None:
    chain = load_chain_spec(file_path="app/eval/chains/chain.v1.yaml")
    result = evaluate_submission(
        EvaluateSubmissionCommand(
            submission_id="sub_00000000000000000000000000",
            normalized_artifact=NormalizedArtifact(
                submission_public_id="sub_00000000000000000000000000",
                assignment_public_id="asg_00000000000000000000000000",
                source_type="api_upload",
                content_markdown="synthetic answer",
                normalization_metadata={},
            ),
            assignment_title="Multitask",
            assignment_description="Synthetic",
            assignment_language="en",
            task_schema=parse_task_schema(_valid_schema()),
            chain_spec=chain,
            effective_model="model:test",
        ),
        llm=_MultitaskLLM(),
    )

    assert result.score_breakdown.task_order() == ["task_1", "task_2"]
    assert result.score_breakdown.task_scores() == {"task_1": 8, "task_2": 9}
    assert result.score_1_10 == 8


@pytest.mark.unit
def test_task_scores_summary_format_is_stable_and_ascii() -> None:
    now = datetime.now(tz=UTC)
    item = SubmissionListItem(
        id=1,
        core=SubmissionListItem.Core(public_id="sub_1", status="evaluated", created_at=now, updated_at=now),
        candidate=SubmissionListItem.Candidate(public_id="cand_1"),
        assignment=SubmissionListItem.Assignment(public_id="asg_1"),
        evaluation=SubmissionListItem.Evaluation(
            score_1_10=8,
            score_breakdown=ScoreBreakdown(
                schema_version="task-criteria:v1",
                tasks=(
                    TaskScoreBreakdown(
                        task_id="task_1",
                        score_1_10=7,
                        weight=0.6,
                        criteria=(
                            CriterionScore(
                                criterion_id="correctness",
                                score=7,
                                reason="ok",
                                weight=1.0,
                            ),
                        ),
                    ),
                    TaskScoreBreakdown(
                        task_id="task_2",
                        score_1_10=9,
                        weight=0.4,
                        criteria=(
                            CriterionScore(
                                criterion_id="coverage",
                                score=9,
                                reason="strong",
                                weight=1.0,
                            ),
                        ),
                    ),
                ),
                overall_score_1_10_derived=8,
            ),
            organizer_feedback=OrganizerFeedback(strengths=(), issues=(), recommendations=()),
            candidate_feedback=CandidateFeedback(summary="ok", what_went_well=(), what_to_improve=()),
            chain_version="chain:v1",
            model="model:v1",
            spec_version="chain-spec:v1",
            response_language="en",
        ),
    )
    export = prepare_export(PrepareExportCommand(items=[item])).export_rows[0]
    assert export.task_scores_summary == "task_1:7;task_2:9"
    assert all(ord(ch) < 128 for ch in export.task_scores_summary)
