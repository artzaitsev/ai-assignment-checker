from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.domain.models import TelegramLinkSettings
from app.domain.use_cases.telegram_entry_links import build_candidate_apply_link, sign_entry_token, verify_entry_token


@pytest.mark.unit
def test_telegram_entry_token_sign_verify_roundtrip() -> None:
    settings = TelegramLinkSettings(
        public_web_base_url="https://portal.example.com",
        signing_secret="test-secret-012345",
        ttl_seconds=60,
    )
    now = datetime(2026, 1, 1, tzinfo=UTC)
    token = sign_entry_token(
        chat_id="chat-1",
        assignment_hint="asg_1",
        settings=settings,
        now=now,
    )
    payload = verify_entry_token(token=token, settings=settings, now=now + timedelta(seconds=30))

    assert payload.v == "v1"
    assert payload.chat_id == "chat-1"
    assert payload.assignment_hint == "asg_1"


@pytest.mark.unit
def test_telegram_entry_token_rejects_tampered_signature() -> None:
    settings = TelegramLinkSettings(
        public_web_base_url="https://portal.example.com",
        signing_secret="test-secret-012345",
        ttl_seconds=60,
    )
    token = sign_entry_token(
        chat_id="chat-1",
        assignment_hint=None,
        settings=settings,
    )
    payload_part, signature_part = token.split(".")
    tampered_payload = ("A" if payload_part[0] != "A" else "B") + payload_part[1:]
    tampered = f"{tampered_payload}.{signature_part}"

    with pytest.raises(ValueError, match="signature"):
        verify_entry_token(token=tampered, settings=settings)


@pytest.mark.unit
def test_telegram_entry_token_rejects_expired_tokens() -> None:
    settings = TelegramLinkSettings(
        public_web_base_url="https://portal.example.com",
        signing_secret="test-secret-012345",
        ttl_seconds=5,
    )
    now = datetime(2026, 1, 1, tzinfo=UTC)
    token = sign_entry_token(
        chat_id="chat-1",
        assignment_hint=None,
        settings=settings,
        now=now,
    )

    with pytest.raises(ValueError, match="expired"):
        verify_entry_token(token=token, settings=settings, now=now + timedelta(seconds=6))


@pytest.mark.unit
def test_build_candidate_apply_link_uses_assignment_path_when_provided() -> None:
    settings = TelegramLinkSettings(
        public_web_base_url="https://portal.example.com",
        signing_secret="test-secret-012345",
        ttl_seconds=60,
    )
    link = build_candidate_apply_link(settings=settings, token="abc.def", assignment_public_id="asg_123")
    assert link == "https://portal.example.com/candidate/assignments/asg_123/apply?token=abc.def"
