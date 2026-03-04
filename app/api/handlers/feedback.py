"""Feedback API handlers."""
from flask import jsonify

from app.api.auth import require_api_key
from app.repositories.postgres import ArtifactRepository
from app.domain.use_cases.deliver import BuildFeedbackUseCase

artifact_repo = ArtifactRepository()
feedback_use_case = BuildFeedbackUseCase(artifact_repo)


@require_api_key
def list_feedback(submission_id: str):
    """Get feedback for a submission."""
    try:
        feedback = feedback_use_case.execute(submission_id)
        
        response = {
            "submission_id": feedback.submission_id,
            "candidate_name": feedback.candidate_name,
            "overall_score": feedback.overall_score,
            "max_score": feedback.max_score,
            "percentage": (feedback.overall_score / feedback.max_score * 100) if feedback.max_score > 0 else 0,
            "sections": [
                {
                    "title": s.title,
                    "content": s.content,
                    "score": s.score,
                    "max_score": s.max_score,
                    "percentage": (s.score / s.max_score * 100) if s.max_score and s.max_score > 0 else None
                }
                for s in feedback.sections
            ],
            "strengths": feedback.strengths,
            "improvements": feedback.improvements,
            "summary": feedback.summary,
            "generated_at": feedback.generated_at.isoformat()
        }
        
        return jsonify(response)
        
    except ValueError as e:
        return jsonify({"error": str(e)}), 404
    except Exception as e:
        return jsonify({"error": f"Failed to get feedback: {str(e)}"}), 500