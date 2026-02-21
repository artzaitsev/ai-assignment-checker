from __future__ import annotations

from app.api.handlers.deps import ApiDeps
from app.api.schemas import CandidateResponse

COMPONENT_ID = "api.create_candidate"


async def create_candidate_handler(
    *,
    first_name: str,
    last_name: str,
    source_type: str | None,
    source_external_id: str | None,
    api_deps: ApiDeps,
) -> CandidateResponse:
    if source_type and source_external_id:
        candidate = await api_deps.repository.get_or_create_candidate_by_source(
            source_type=source_type,
            source_external_id=source_external_id,
            first_name=first_name,
            last_name=last_name,
            metadata_json={"entrypoint": "api"},
        )
    else:
        candidate = await api_deps.repository.create_candidate(
            first_name=first_name,
            last_name=last_name,
        )
    return CandidateResponse(
        candidate_public_id=candidate.candidate_public_id,
        first_name=candidate.first_name,
        last_name=candidate.last_name,
    )
