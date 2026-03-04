"""Submission status API handlers."""
from typing import Dict, Any
from flask import jsonify, request

from app.api.auth import require_api_key
from app.repositories.postgres import SubmissionRepository, ArtifactRepository
from app.domain.models import SubmissionStatus

submission_repo = SubmissionRepository()
artifact_repo = ArtifactRepository()


@require_api_key
def get_submission_status(submission_id: str):
    """Get submission status with transitions and artifacts."""
    submission = submission_repo.get_by_id(submission_id)
    if not submission:
        return jsonify({"error": "Submission not found"}), 404
    
    # Get all artifacts
    artifacts = artifact_repo.get_by_submission(submission_id)
    
    # Build response
    response = {
        "submission_id": submission.id,
        "status": submission.status.value,
        "created_at": submission.created_at.isoformat() if submission.created_at else None,
        "updated_at": submission.updated_at.isoformat() if submission.updated_at else None,
        "metadata": submission.metadata or {},
        "transitions": _get_possible_transitions(submission.status),
        "artifacts": [
            {
                "id": a.id,
                "type": a.artifact_type,
                "created_at": a.created_at.isoformat() if a.created_at else None,
                "summary": _get_artifact_summary(a)
            }
            for a in artifacts
        ]
    }
    
    return jsonify(response)


def _get_possible_transitions(current_status: SubmissionStatus) -> list:
    """Get possible next statuses."""
    transitions = {
        SubmissionStatus.PENDING: ["processing"],
        SubmissionStatus.PROCESSING: ["evaluating", "failed"],
        SubmissionStatus.EVALUATING: ["exports", "failed"],
        SubmissionStatus.EXPORTS: ["delivered", "failed"],
        SubmissionStatus.DELIVERED: [],
        SubmissionStatus.FAILED: ["processing"]  # Can retry
    }
    
    return transitions.get(current_status, [])


def _get_artifact_summary(artifact) -> Dict[str, Any]:
    """Get artifact summary based on type."""
    summary = {"type": artifact.artifact_type}
    
    if artifact.artifact_type == "feedback":
        data = artifact.data or {}
        summary.update({
            "overall_score": data.get("overall_score"),
            "max_score": data.get("max_score")
        })
    elif artifact.artifact_type == "export_reference":
        data = artifact.data or {}
        summary.update({
            "format": data.get("format"),
            "size": data.get("size")
        })
    
    return summary