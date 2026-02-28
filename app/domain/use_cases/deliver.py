from __future__ import annotations

from typing import cast

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
        headline = "Your submission was reviewed."
    else:
        headline = f"Your submission was reviewed. Score: {cmd.score_1_10}/10."

    summary = (cmd.summary or "Review details are available in your dashboard.").strip()
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

        candidate_feedback_raw = evaluation.candidate_feedback_json if evaluation else None
        organizer_feedback_raw = evaluation.organizer_feedback_json if evaluation else None
        criteria_json_raw = evaluation.criteria_scores_json if evaluation else None
        candidate_feedback: dict[str, object] = (
            dict(candidate_feedback_raw) if isinstance(candidate_feedback_raw, dict) else {}
        )
        organizer_feedback: dict[str, object] = (
            dict(organizer_feedback_raw) if isinstance(organizer_feedback_raw, dict) else {}
        )
        criteria_json: dict[str, object] = dict(criteria_json_raw) if isinstance(criteria_json_raw, dict) else {}
        criteria_items_raw = criteria_json.get("items", []) if isinstance(criteria_json, dict) else []
        criteria_items = criteria_items_raw if isinstance(criteria_items_raw, list) else []
        criteria_summary = "; ".join(
            f"{criterion.get('id')}:{criterion.get('score')}"
            for criterion in criteria_items
            if isinstance(criterion, dict)
        )

        strengths = _join_text_list(organizer_feedback.get("strengths"))
        issues = _join_text_list(organizer_feedback.get("issues"))
        recommendations = _join_text_list(organizer_feedback.get("recommendations"))

        rows.append(
            ExportRowArtifact(
                candidate_identifier=item.candidate.public_id if item.candidate else "",
                assignment_identifier=item.assignment.public_id if item.assignment else "",
                score_1_10=evaluation.score_1_10,
                criteria_summary=criteria_summary,
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


def _join_text_list(value: object) -> str:
    if not isinstance(value, list):
        return ""
    return "; ".join(str(item) for item in value)
