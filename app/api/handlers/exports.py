"""Export API handlers."""
from flask import jsonify, request, send_file

from app.api.auth import require_api_key
from app.core.storage import StorageService
from app.repositories.postgres import ArtifactRepository
from app.domain.use_cases.deliver import PrepareExportUseCase, SubmissionRepository

submission_repo = SubmissionRepository()
artifact_repo = ArtifactRepository()
storage = StorageService()
export_use_case = PrepareExportUseCase(submission_repo, artifact_repo, storage)


@require_api_key
def export_results(submission_id: str):
    """Export results for a submission."""
    format = request.args.get("format", "csv")
    
    try:
        export_result = export_use_case.execute(submission_id, format)
        
        response = {
            "export_ref": export_result.export_ref,
            "format": export_result.format,
            "size": export_result.size,
            "created_at": export_result.created_at.isoformat(),
            "download_url": f"/api/v1/exports/{export_result.export_ref}/download"
        }
        
        return jsonify(response), 201
        
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        return jsonify({"error": f"Failed to create export: {str(e)}"}), 500


@require_api_key
def download_export(export_ref: str):
    """Download an export file."""
    try:
        # Get file from storage
        file_path = storage.get_path(export_ref)
        if not file_path or not storage.exists(export_ref):
            return jsonify({"error": "Export not found"}), 404
        
        # Send file
        return send_file(
            file_path,
            as_attachment=True,
            download_name=export_ref.split("/")[-1]
        )
        
    except Exception as e:
        return jsonify({"error": f"Failed to download export: {str(e)}"}), 500


@require_api_key
def list_exports(submission_id: str):
    """List all exports for a submission."""
    try:
        artifacts = artifact_repo.get_by_submission(submission_id)
        exports = [
            a.data for a in artifacts 
            if a.artifact_type == "export_reference" and a.data
        ]
        
        return jsonify({
            "submission_id": submission_id,
            "exports": exports
        })
        
    except Exception as e:
        return jsonify({"error": f"Failed to list exports: {str(e)}"}), 500