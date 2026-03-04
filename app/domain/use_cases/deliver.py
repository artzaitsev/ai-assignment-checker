"""Domain use cases for delivery stage."""
import logging
from datetime import datetime
from typing import Dict, Any, Optional, List
from dataclasses import dataclass

from app.domain.models import Submission, SubmissionStatus, Artifact
from app.repositories.postgres import SubmissionRepository, ArtifactRepository
from app.core.storage import StorageService

logger = logging.getLogger(__name__)


@dataclass
class FeedbackSection:
    """Feedback section structure."""
    title: str
    content: str
    score: Optional[float] = None
    max_score: Optional[float] = None


@dataclass
class Feedback:
    """Complete feedback structure."""
    submission_id: str
    candidate_name: str
    overall_score: float
    max_score: float
    sections: List[FeedbackSection]
    strengths: List[str]
    improvements: List[str]
    summary: str
    generated_at: datetime
    evaluation_id: Optional[str] = None


class BuildFeedbackUseCase:
    """Build human-readable feedback from evaluation results."""

    def __init__(self, artifact_repo: ArtifactRepository):
        self.artifact_repo = artifact_repo

    def execute(self, submission_id: str) -> Feedback:
        """Build feedback from artifacts."""
        # Get all artifacts for this submission
        artifacts = self.artifact_repo.get_by_submission(submission_id)
        
        if not artifacts:
            raise ValueError(f"No artifacts found for submission {submission_id}")
        
        # Find evaluation results artifact
        eval_artifact = next(
            (a for a in artifacts if a.artifact_type == "evaluation_results"),
            None
        )
        
        if not eval_artifact:
            raise ValueError(f"No evaluation results found for submission {submission_id}")
        
        # Parse evaluation data
        eval_data = eval_artifact.data or {}
        
        # Build feedback structure
        return self._build_from_evaluation(eval_data, submission_id)

    def _build_from_evaluation(self, eval_data: Dict[str, Any], submission_id: str) -> Feedback:
        """Build feedback from evaluation data."""
        # Extract scores
        scores = eval_data.get("scores", {})
        overall_score = scores.get("overall", 0.0)
        max_score = scores.get("max_score", 100.0)
        
        # Build sections
        sections = []
        for criterion, score in scores.get("criteria", {}).items():
            if isinstance(score, dict):
                sections.append(FeedbackSection(
                    title=criterion.replace("_", " ").title(),
                    content=score.get("feedback", "No detailed feedback"),
                    score=score.get("value"),
                    max_score=score.get("max")
                ))
            else:
                sections.append(FeedbackSection(
                    title=criterion.replace("_", " ").title(),
                    content=f"Score: {score}",
                    score=score,
                    max_score=100.0
                ))
        
        # Extract strengths and improvements
        strengths = eval_data.get("strengths", [])
        improvements = eval_data.get("improvements", [])
        
        # Generate summary
        summary = eval_data.get("summary", self._generate_summary(overall_score, max_score))
        
        return Feedback(
            submission_id=submission_id,
            candidate_name=eval_data.get("candidate_name", "Candidate"),
            overall_score=overall_score,
            max_score=max_score,
            sections=sections,
            strengths=strengths,
            improvements=improvements,
            summary=summary,
            generated_at=datetime.utcnow(),
            evaluation_id=eval_data.get("evaluation_id")
        )

    def _generate_summary(self, score: float, max_score: float) -> str:
        """Generate a summary based on score."""
        percentage = (score / max_score) * 100 if max_score > 0 else 0
        
        if percentage >= 90:
            return "Excellent performance across all criteria."
        elif percentage >= 75:
            return "Good performance with some areas for improvement."
        elif percentage >= 60:
            return "Satisfactory performance, several areas need attention."
        else:
            return "Below expected performance, significant improvement needed."


@dataclass
class ExportResult:
    """Export operation result."""
    export_ref: str
    format: str
    path: str
    size: int
    created_at: datetime


class PrepareExportUseCase:
    """Prepare export in requested format."""

    SUPPORTED_FORMATS = ["csv", "json"]

    def __init__(
        self,
        submission_repo: SubmissionRepository,
        artifact_repo: ArtifactRepository,
        storage: StorageService
    ):
        self.submission_repo = submission_repo
        self.artifact_repo = artifact_repo
        self.storage = storage

    def execute(self, submission_id: str, format: str = "csv") -> ExportResult:
        """Prepare export for submission."""
        if format not in self.SUPPORTED_FORMATS:
            raise ValueError(f"Unsupported format: {format}. Supported: {self.SUPPORTED_FORMATS}")
        
        # Get submission data
        submission = self.submission_repo.get_by_id(submission_id)
        if not submission:
            raise ValueError(f"Submission {submission_id} not found")
        
        # Get all artifacts
        artifacts = self.artifact_repo.get_by_submission(submission_id)
        
        # Prepare export data
        export_data = self._prepare_export_data(submission, artifacts)
        
        # Convert to requested format
        if format == "csv":
            content = self._to_csv(export_data)
        else:  # json
            content = self._to_json(export_data)
        
        # Store export
        export_ref = f"exports/{submission_id}_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.{format}"
        path = self.storage.save(export_ref, content)
        
        return ExportResult(
            export_ref=export_ref,
            format=format,
            path=path,
            size=len(content),
            created_at=datetime.utcnow()
        )

    def _prepare_export_data(self, submission: Submission, artifacts: List[Artifact]) -> Dict[str, Any]:
        """Prepare data for export."""
        data = {
            "submission_id": submission.id,
            "status": submission.status.value,
            "created_at": submission.created_at.isoformat() if submission.created_at else None,
            "updated_at": submission.updated_at.isoformat() if submission.updated_at else None,
            "metadata": submission.metadata or {},
        }
        
        # Add artifacts data
        artifacts_data = {}
        for artifact in artifacts:
            artifacts_data[artifact.artifact_type] = {
                "id": artifact.id,
                "created_at": artifact.created_at.isoformat() if artifact.created_at else None,
                "data": artifact.data
            }
        data["artifacts"] = artifacts_data
        
        return data

    def _to_csv(self, data: Dict[str, Any]) -> str:
        """Convert data to CSV format."""
        import csv
        from io import StringIO
        
        output = StringIO()
        writer = csv.writer(output)
        
        # Write header
        writer.writerow(["field", "value"])
        
        # Flatten data for CSV
        def flatten_dict(d, parent_key=""):
            items = []
            for k, v in d.items():
                new_key = f"{parent_key}.{k}" if parent_key else k
                if isinstance(v, dict):
                    items.extend(flatten_dict(v, new_key).items())
                else:
                    items.append((new_key, str(v) if v is not None else ""))
            return dict(items)
        
        flat_data = flatten_dict(data)
        for key, value in flat_data.items():
            writer.writerow([key, value])
        
        return output.getvalue()

    def _to_json(self, data: Dict[str, Any]) -> str:
        """Convert data to JSON format."""
        import json
        return json.dumps(data, indent=2, default=str)