from __future__ import annotations

from typing import cast

from app.domain.evaluation_contracts import ScoreBreakdown
from app.lib.artifacts.types import ExportRowArtifact
from app.domain.dto import (
    BuildFeedbackCommand,
    BuildFeedbackResult,
    PrepareExportCommand,
    PrepareExportResult,
)

COMPONENT_ID_FEEDBACK = "domain.feedback.build"
COMPONENT_ID_EXPORT = "domain.export.prepare"


def build_feedback(cmd: BuildFeedbackCommand) -> BuildFeedbackResult:
    """Build delivery notification payload from evaluated submission."""
    if cmd.score_1_10 is None:
        headline = "Ваша работа проверена."
    else:
        headline = f"Ваша работа проверена. Оценка: {cmd.score_1_10}/10."

    summary = (cmd.summary or "Подробности проверки доступны в Вашем личном кабинете.").strip()
    return BuildFeedbackResult(message_text=f"{headline} {summary}")


def prepare_export(cmd: PrepareExportCommand) -> PrepareExportResult:
    """Build CSV-compatible export rows with reproducibility fields."""
    rows: list[ExportRowArtifact] = []
    for item in cmd.items:
        evaluation = item.evaluation
        # Export rows are contract-valid only for submissions that already have
        # persisted evaluation payload and required reproducibility subset.
        if evaluation is None or evaluation.score_1_10 is None:
            continue
        if not all(
            [
                evaluation.chain_version,
                evaluation.spec_version,
                evaluation.model,
                evaluation.response_language,
            ]
        ):
            continue
        chain_version = evaluation.chain_version
        spec_version = evaluation.spec_version
        model = evaluation.model
        response_language = evaluation.response_language
        if (
            chain_version is None
            or spec_version is None
            or model is None
            or response_language is None
        ):
            continue
        chain_version = cast(str, chain_version)
        spec_version = cast(str, spec_version)
        model = cast(str, model)
        response_language = cast(str, response_language)

        candidate_feedback = evaluation.candidate_feedback
        organizer_feedback = evaluation.organizer_feedback
        score_breakdown = evaluation.score_breakdown
        if candidate_feedback is None or organizer_feedback is None or score_breakdown is None:
            continue

        criteria_summary = "; ".join(
            f"{criterion.criterion_id}:{criterion.score}"
            for criterion in score_breakdown.criterion_items()
        )
        task_scores_summary = _build_task_scores_summary(score_breakdown)

        strengths = _join_text_list(organizer_feedback.strengths)
        issues = _join_text_list(organizer_feedback.issues)
        recommendations = _join_text_list(organizer_feedback.recommendations)

        rows.append(
            ExportRowArtifact(
                candidate_identifier=item.candidate.public_id if item.candidate else "",
                assignment_identifier=item.assignment.public_id if item.assignment else "",
                score_1_10=evaluation.score_1_10,
                criteria_summary=criteria_summary,
                task_scores_summary=task_scores_summary,
                strengths=strengths,
                issues=issues,
                recommendations=recommendations,
                chain_version=chain_version,
                model=model,
                spec_version=spec_version,
                response_language=response_language,
            )
        )

    return PrepareExportResult(export_rows=rows)


def _join_text_list(value: tuple[str, ...] | list[str]) -> str:
    return "; ".join(value)


def _build_task_scores_summary(score_breakdown: ScoreBreakdown) -> str:
    parts: list[str] = []
    for task in score_breakdown.tasks:
        task_id = task.task_id
        score = task.score_1_10
        safe_task_id = task_id.encode("ascii", errors="ignore").decode("ascii")
        parts.append(f"{safe_task_id}:{score}")
    return ";".join(parts)
