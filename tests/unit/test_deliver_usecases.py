"""Unit tests for delivery use cases."""
import pytest
from datetime import datetime
from unittest.mock import Mock, patch

from app.domain.use_cases.deliver import BuildFeedbackUseCase, PrepareExportUseCase
from app.domain.models import Submission, SubmissionStatus, Artifact


class TestBuildFeedbackUseCase:
    """Test BuildFeedbackUseCase."""

    def test_build_feedback_success(self):
        """Test successful feedback building."""
        # Mock repository
        mock_repo = Mock()
        mock_repo.get_by_submission.return_value = [
            Artifact(
                id="art1",
                submission_id="sub1",
                artifact_type="evaluation_results",
                data={
                    "scores": {
                        "overall": 85,
                        "max_score": 100,
                        "criteria": {
                            "technical": {"value": 45, "max": 50, "feedback": "Good technical skills"},
                            "communication": {"value": 40, "max": 50, "feedback": "Clear communication"}
                        }
                    },
                    "strengths": ["Strong technical knowledge"],
                    "improvements": ["Could improve documentation"],
                    "summary": "Good overall performance",
                    "candidate_name": "John Doe"
                },
                created_at=datetime.utcnow()
            )
        ]

        use_case = BuildFeedbackUseCase(mock_repo)
        feedback = use_case.execute("sub1")

        assert feedback.submission_id == "sub1"
        assert feedback.candidate_name == "John Doe"
        assert feedback.overall_score == 85
        assert len(feedback.sections) == 2
        assert len(feedback.strengths) == 1
        assert "Strong technical knowledge" in feedback.strengths

    def test_build_feedback_no_artifacts(self):
        """Test feedback building with no artifacts."""
        mock_repo = Mock()
        mock_repo.get_by_submission.return_value = []

        use_case = BuildFeedbackUseCase(mock_repo)
        
        with pytest.raises(ValueError, match="No artifacts found"):
            use_case.execute("sub1")

    def test_build_feedback_no_evaluation(self):
        """Test feedback building with no evaluation results."""
        mock_repo = Mock()
        mock_repo.get_by_submission.return_value = [
            Artifact(
                id="art1",
                submission_id="sub1",
                artifact_type="other",
                data={},
                created_at=datetime.utcnow()
            )
        ]

        use_case = BuildFeedbackUseCase(mock_repo)
        
        with pytest.raises(ValueError, match="No evaluation results found"):
            use_case.execute("sub1")


class TestPrepareExportUseCase:
    """Test PrepareExportUseCase."""

    def test_prepare_csv_export(self):
        """Test CSV export preparation."""
        # Mocks
        mock_submission_repo = Mock()
        mock_submission_repo.get_by_id.return_value = Submission(
            id="sub1",
            status=SubmissionStatus.EVALUATING,
            metadata={"test": "data"},
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow()
        )

        mock_artifact_repo = Mock()
        mock_artifact_repo.get_by_submission.return_value = [
            Artifact(
                id="art1",
                submission_id="sub1",
                artifact_type="evaluation_results",
                data={"score": 85},
                created_at=datetime.utcnow()
            )
        ]

        mock_storage = Mock()
        mock_storage.save.return_value = "/path/to/export.csv"

        use_case = PrepareExportUseCase(
            mock_submission_repo,
            mock_artifact_repo,
            mock_storage
        )

        result = use_case.execute("sub1", format="csv")

        assert result.format == "csv"
        assert result.export_ref.startswith("exports/sub1_")
        assert result.size > 0
        mock_storage.save.assert_called_once()

    def test_prepare_json_export(self):
        """Test JSON export preparation."""
        mock_submission_repo = Mock()
        mock_submission_repo.get_by_id.return_value = Submission(
            id="sub1",
            status=SubmissionStatus.EVALUATING,
            metadata={},
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow()
        )

        mock_artifact_repo = Mock()
        mock_artifact_repo.get_by_submission.return_value = []

        mock_storage = Mock()
        mock_storage.save.return_value = "/path/to/export.json"

        use_case = PrepareExportUseCase(
            mock_submission_repo,
            mock_artifact_repo,
            mock_storage
        )

        result = use_case.execute("sub1", format="json")

        assert result.format == "json"
        assert result.export_ref.endswith(".json")

    def test_unsupported_format(self):
        """Test unsupported format."""
        use_case = PrepareExportUseCase(Mock(), Mock(), Mock())
        
        with pytest.raises(ValueError, match="Unsupported format"):
            use_case.execute("sub1", format="pdf")

    def test_submission_not_found(self):
        """Test export with non-existent submission."""
        mock_submission_repo = Mock()
        mock_submission_repo.get_by_id.return_value = None

        use_case = PrepareExportUseCase(
            mock_submission_repo,
            Mock(),
            Mock()
        )

        with pytest.raises(ValueError, match="not found"):
            use_case.execute("sub1")