from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.domain.models import ApplySessionSettings
from app.domain.use_cases.apply_session import sign_apply_session, verify_apply_session


@pytest.mark.unit
def test_apply_session_roundtrip() -> None:
    settings = ApplySessionSettings(signing_secret="apply-session-secret-123", ttl_seconds=120)
    now = datetime(2026, 1, 1, tzinfo=UTC)

    token = sign_apply_session(
        chat_id="chat-1",
        assignment_hint="asg_1",
        settings=settings,
        now=now,
    )
    payload = verify_apply_session(token=token, settings=settings, now=now + timedelta(seconds=60))

    assert payload.chat_id == "chat-1"
    assert payload.assignment_hint == "asg_1"


@pytest.mark.unit
def test_apply_session_rejects_tampered_payload() -> None:
    settings = ApplySessionSettings(signing_secret="apply-session-secret-123", ttl_seconds=120)
    token = sign_apply_session(
        chat_id="chat-1",
        assignment_hint=None,
        settings=settings,
    )
    payload_part, signature_part = token.split(".")
    tampered_payload = ("A" if payload_part[0] != "A" else "B") + payload_part[1:]

    with pytest.raises(ValueError, match="signature"):
        verify_apply_session(token=f"{tampered_payload}.{signature_part}", settings=settings)


@pytest.mark.unit
def test_apply_session_rejects_expired_token() -> None:
    settings = ApplySessionSettings(signing_secret="apply-session-secret-123", ttl_seconds=5)
    now = datetime(2026, 1, 1, tzinfo=UTC)
    token = sign_apply_session(
        chat_id="chat-1",
        assignment_hint=None,
        settings=settings,
        now=now,
    )

    with pytest.raises(ValueError, match="expired"):
        verify_apply_session(token=token, settings=settings, now=now + timedelta(seconds=6))
