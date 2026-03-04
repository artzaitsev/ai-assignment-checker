"""Integration tests for delivery pipeline."""
import pytest
import json
from datetime import datetime

from app.core.queue import Message, QueueService
from app.core.storage import StorageService
from app.domain.models import Submission, SubmissionStatus
from app.repositories.postgres import SubmissionRepository, ArtifactRepository
from app.workers.handlers.deliver import DeliverHandler


@pytest.fixture
def setup_submission(db_connection):
    """Create a submission in EXPORTS state."""
    repo = SubmissionRepository()
    
    # Create submission
    submission = repo.create(
        metadata={"candidate": "Test Candidate", "test": True}
    )
    
    # Update to EXPORTS
    repo.update_status(submission.id, SubmissionStatus.EXPORTS)
    
    return submission


@pytest.fixture
def setup_artifacts(db_connection, setup_submission):
    """Create evaluation artifacts."""
    artifact_repo = ArtifactRepository()
    submission = setup_submission
    
    # Create evaluation results
    artifact_repo.create(
        submission_id=submission.id,
        artifact_type="evaluation_results",
        data={
            "scores": {
                "overall": 92,
                "max_score": 100,
                "criteria": {
                    "technical": {
                        "value": 47,
                        "max": 50,
                        "feedback": "Excellent technical skills"
                    },
                    "communication": {
                        "value": 45,
                        "max": 50,
                        "feedback": "Very clear communication"
                    }
                }
            },
            "strengths": [
                "Strong problem-solving skills",
                "Good system design understanding"
            ],
            "improvements": [
                "Could improve documentation practices"
            ],
            "summary": "Outstanding candidate with strong technical skills",
            "candidate_name": "Test Candidate",
            "evaluation_id": "eval_123"
        }
    )
    
    return submission


def test_full_delivery_pipeline(db_connection, setup_artifacts):
    """Test complete delivery pipeline."""
    submission = setup_artifacts
    
    # Initialize components
    submission_repo = SubmissionRepository()
    artifact_repo = ArtifactRepository()
    storage = StorageService()
    queue = QueueService()
    
    handler = DeliverHandler(
        submission_repo,
        artifact_repo,
        storage,
        queue
    )
    
    # Process delivery
    message = Message(
        id="test_msg",
        type="submission.deliver",
        data={"submission_id": submission.id},
        timestamp=datetime.utcnow()
    )
    
    result = handler.handle(message)
    
    # Verify result
    assert result["status"] == "success"
    assert result["submission_id"] == submission.id
    assert "feedback_id" in result
    assert "export_ref" in result
    
    # Check submission status
    updated = submission_repo.get_by_id(submission.id)
    assert updated.status == SubmissionStatus.DELIVERED
    assert updated.metadata.get("delivered_at") is not None
    
    # Check artifacts
    artifacts = artifact_repo.get_by_submission(submission.id)
    artifact_types = [a.artifact_type for a in artifacts]
    
    assert "feedback" in artifact_types
    assert "export_reference" in artifact_types
    
    # Get feedback artifact
    feedback = next(a for a in artifacts if a.artifact_type == "feedback")
    assert feedback.data is not None
    assert "overall_score" in feedback.data
    assert feedback.data["overall_score"] == 92
    
    # Get export reference
    export_ref = next(a for a in artifacts if a.artifact_type == "export_reference")
    assert export_ref.data is not None
    assert "export_ref" in export_ref.data
    assert export_ref.data["format"] == "csv"
    
    # Verify export file exists
    export_path = export_ref.data["export_ref"]
    assert storage.exists(export_path)
    
    # Read export content
    content = storage.read(export_path)
    assert content is not None
    assert "submission_id" in content
    assert "92" in content  # Score should be in export


def test_delivery_error_handling(db_connection, setup_submission):
    """Test error handling in delivery."""
    submission = setup_submission
    
    # Don't create any artifacts - should cause error
    
    submission_repo = SubmissionRepository()
    artifact_repo = ArtifactRepository()
    storage = StorageService()
    queue = QueueService()
    
    handler = DeliverHandler(
        submission_repo,
        artifact_repo,
        storage,
        queue
    )
    
    message = Message(
        id="test_msg",
        type="submission.deliver",
        data={"submission_id": submission.id},
        timestamp=datetime.utcnow()
    )
    
    # Should raise exception
    with pytest.raises(Exception):
        handler.handle(message)
    
    # Check submission status
    updated = submission_repo.get_by_id(submission.id)
    assert updated.status == SubmissionStatus.FAILED
    assert updated.metadata.get("error") is not None


def test_api_endpoints(client, db_connection, setup_artifacts):
    """Test API endpoints after delivery."""
    submission = setup_artifacts
    
    # First complete delivery
    submission_repo = SubmissionRepository()
    artifact_repo = ArtifactRepository()
    storage = StorageService()
    queue = QueueService()
    
    handler = DeliverHandler(
        submission_repo,
        artifact_repo,
        storage,
        queue
    )
    
    message = Message(
        id="test_msg",
        type="submission.deliver",
        data={"submission_id": submission.id},
        timestamp=datetime.utcnow()
    )
    
    handler.handle(message)
    
    # Test status endpoint
    response = client.get(f"/api/v1/submissions/{submission.id}/status")
    assert response.status_code == 200
    data = json.loads(response.data)
    assert data["status"] == "delivered"
    assert "artifacts" in data
    assert len(data["artifacts"]) >= 2
    
    # Test feedback endpoint
    response = client.get(f"/api/v1/submissions/{submission.id}/feedback")
    assert response.status_code == 200
    data = json.loads(response.data)
    assert data["submission_id"] == submission.id
    assert data["overall_score"] == 92
    assert len(data["sections"]) == 2
    assert len(data["strengths"]) == 2
    
    # Test export creation
    response = client.post(f"/api/v1/submissions/{submission.id}/exports?format=csv")
    assert response.status_code == 201
    data = json.loads(response.data)
    assert "export_ref" in data
    assert data["format"] == "csv"
    
    # Test exports list
    response = client.get(f"/api/v1/submissions/{submission.id}/exports")
    assert response.status_code == 200
    data = json.loads(response.data)
    assert data["submission_id"] == submission.id
    assert len(data["exports"]) >= 1