"""Delivery worker handler."""
import logging
from datetime import datetime
from typing import Dict, Any

from app.core.queue import Message, QueueService
from app.core.storage import StorageService
from app.domain.models import Submission, SubmissionStatus
from app.domain.use_cases.deliver import BuildFeedbackUseCase, PrepareExportUseCase
from app.repositories.postgres import SubmissionRepository, ArtifactRepository
from app.workers.handlers.base import BaseHandler

logger = logging.getLogger(__name__)


class DeliverHandler(BaseHandler):
    """Handler for delivery stage."""

    def __init__(
        self,
        submission_repo: SubmissionRepository,
        artifact_repo: ArtifactRepository,
        storage: StorageService,
        queue_service: QueueService
    ):
        self.submission_repo = submission_repo
        self.artifact_repo = artifact_repo
        self.storage = storage
        self.queue_service = queue_service
        self.feedback_use_case = BuildFeedbackUseCase(artifact_repo)
        self.export_use_case = PrepareExportUseCase(submission_repo, artifact_repo, storage)

    def handle(self, message: Message) -> Dict[str, Any]:
        """Process delivery for a submission."""
        submission_id = message.data.get("submission_id")
        if not submission_id:
            raise ValueError("Missing submission_id in message")

        logger.info(f"Starting delivery for submission {submission_id}")

        try:
            # Get submission
            submission = self.submission_repo.get_by_id(submission_id)
            if not submission:
                raise ValueError(f"Submission {submission_id} not found")

            # Ensure we're in exports stage
            if submission.status != SubmissionStatus.EXPORTS:
                logger.warning(
                    f"Submission {submission_id} is in {submission.status} state, "
                    f"expected EXPORTS"
                )
                # Still try to deliver if we have artifacts

            # 1. Build feedback
            logger.info(f"Building feedback for {submission_id}")
            feedback = self.feedback_use_case.execute(submission_id)
            
            # Save feedback as artifact
            feedback_artifact = self.artifact_repo.create(
                submission_id=submission_id,
                artifact_type="feedback",
                data={
                    "overall_score": feedback.overall_score,
                    "max_score": feedback.max_score,
                    "sections": [
                        {
                            "title": s.title,
                            "content": s.content,
                            "score": s.score,
                            "max_score": s.max_score
                        }
                        for s in feedback.sections
                    ],
                    "strengths": feedback.strengths,
                    "improvements": feedback.improvements,
                    "summary": feedback.summary,
                    "generated_at": feedback.generated_at.isoformat()
                }
            )
            logger.info(f"Feedback saved as artifact {feedback_artifact.id}")

            # 2. Prepare export
            logger.info(f"Preparing export for {submission_id}")
            export_result = self.export_use_case.execute(submission_id, format="csv")
            
            # Save export reference as artifact
            export_artifact = self.artifact_repo.create(
                submission_id=submission_id,
                artifact_type="export_reference",
                data={
                    "export_ref": export_result.export_ref,
                    "format": export_result.format,
                    "path": export_result.path,
                    "size": export_result.size,
                    "created_at": export_result.created_at.isoformat()
                }
            )
            logger.info(f"Export saved as artifact {export_artifact.id}")

            # 3. Update submission status to DELIVERED
            submission = self.submission_repo.update_status(
                submission_id, 
                SubmissionStatus.DELIVERED
            )
            
            # 4. Add metadata
            self.submission_repo.update_metadata(
                submission_id,
                {
                    "delivered_at": datetime.utcnow().isoformat(),
                    "feedback_id": feedback_artifact.id,
                    "export_id": export_artifact.id,
                    "export_ref": export_result.export_ref
                }
            )

            logger.info(f"Successfully delivered submission {submission_id}")

            return {
                "status": "success",
                "submission_id": submission_id,
                "feedback_id": feedback_artifact.id,
                "export_ref": export_result.export_ref
            }

        except Exception as e:
            logger.error(f"Delivery failed for submission {submission_id}: {str(e)}", exc_info=True)
            
            # Update submission status to FAILED
            try:
                self.submission_repo.update_status(
                    submission_id,
                    SubmissionStatus.FAILED,
                    error=str(e)
                )
            except Exception as update_error:
                logger.error(f"Failed to update status: {update_error}")

            # Re-raise for retry mechanism
            raise