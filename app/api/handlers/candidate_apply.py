from __future__ import annotations

from app.api.handlers.deps import ApiDeps
from app.api.handlers.submissions import create_submission_with_file_handler
from app.domain.models import CandidateSourceType
from app.domain.use_cases.apply_session import ApplySessionPayload, sign_apply_session, verify_apply_session
from app.domain.use_cases.telegram_entry_links import verify_entry_token


async def exchange_entry_token_for_session(
    deps: ApiDeps,
    *,
    entry_token: str,
) -> str:
    telegram_link_settings = deps.telegram_link_settings
    if telegram_link_settings is None:
        raise ValueError("telegram link settings are not configured")
    apply_session_settings = deps.apply_session_settings
    if apply_session_settings is None:
        raise ValueError("apply session settings are not configured")

    entry_payload = verify_entry_token(token=entry_token, settings=telegram_link_settings)
    return sign_apply_session(
        chat_id=entry_payload.chat_id,
        assignment_hint=entry_payload.assignment_hint,
        settings=apply_session_settings,
    )


def validate_apply_session(deps: ApiDeps, *, session_token: str | None) -> ApplySessionPayload:
    apply_session_settings = deps.apply_session_settings
    if apply_session_settings is None:
        raise ValueError("apply session settings are not configured")
    if session_token is None:
        raise ValueError("apply session is required")
    return verify_apply_session(token=session_token, settings=apply_session_settings)


async def submit_candidate_apply_form(
    deps: ApiDeps,
    *,
    session: ApplySessionPayload,
    first_name: str,
    last_name: str,
    assignment_public_id: str,
    filename: str,
    payload: bytes,
) -> str:
    candidate = await deps.repository.get_or_create_candidate_by_source(
        source_type=CandidateSourceType.TELEGRAM_CHAT,
        source_external_id=session.chat_id,
        first_name=first_name,
        last_name=last_name,
        metadata_json={
            "entrypoint": "candidate_apply",
            "session_nonce": session.nonce,
        },
    )

    result = await create_submission_with_file_handler(
        deps,
        filename=filename,
        payload=payload,
        candidate_public_id=candidate.candidate_public_id,
        assignment_public_id=assignment_public_id,
        source_external_id=f"webapply-{session.nonce}",
    )
    return result.submission_id
