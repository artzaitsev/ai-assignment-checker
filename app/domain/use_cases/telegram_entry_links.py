from __future__ import annotations

import base64
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
import hmac
import json
import secrets
from urllib.parse import urlencode

from app.domain.models import TelegramLinkSettings

TOKEN_VERSION = "v1"


@dataclass(frozen=True)
class TelegramEntryTokenPayload:
    v: str
    iat: int
    exp: int
    nonce: str
    chat_id: str
    assignment_hint: str | None


def sign_entry_token(
    *,
    chat_id: str,
    assignment_hint: str | None,
    settings: TelegramLinkSettings,
    now: datetime | None = None,
) -> str:
    issued_at = int((now or datetime.now(tz=UTC)).timestamp())
    payload = TelegramEntryTokenPayload(
        v=TOKEN_VERSION,
        iat=issued_at,
        exp=issued_at + settings.ttl_seconds,
        nonce=secrets.token_urlsafe(12),
        chat_id=chat_id,
        assignment_hint=assignment_hint,
    )
    payload_json = json.dumps(payload.__dict__, sort_keys=True, separators=(",", ":")).encode("utf-8")
    payload_b64 = _b64url_encode(payload_json)
    signature = hmac.new(settings.signing_secret.encode("utf-8"), payload_b64.encode("ascii"), sha256).digest()
    signature_b64 = _b64url_encode(signature)
    return f"{payload_b64}.{signature_b64}"


def verify_entry_token(
    *,
    token: str,
    settings: TelegramLinkSettings,
    now: datetime | None = None,
) -> TelegramEntryTokenPayload:
    parts = token.split(".")
    if len(parts) != 2:
        raise ValueError("token format is invalid")

    payload_b64, signature_b64 = parts
    expected_signature = hmac.new(
        settings.signing_secret.encode("utf-8"),
        payload_b64.encode("ascii"),
        sha256,
    ).digest()
    actual_signature = _b64url_decode(signature_b64)
    if not hmac.compare_digest(actual_signature, expected_signature):
        raise ValueError("token signature is invalid")

    payload_raw = _b64url_decode(payload_b64)
    parsed = json.loads(payload_raw)
    payload = TelegramEntryTokenPayload(
        v=str(parsed.get("v", "")),
        iat=int(parsed.get("iat", 0)),
        exp=int(parsed.get("exp", 0)),
        nonce=str(parsed.get("nonce", "")),
        chat_id=str(parsed.get("chat_id", "")),
        assignment_hint=_as_optional_str(parsed.get("assignment_hint")),
    )
    if payload.v != TOKEN_VERSION:
        raise ValueError("token version is unsupported")
    if not payload.nonce:
        raise ValueError("token nonce is missing")
    if not payload.chat_id:
        raise ValueError("token chat_id is missing")

    current_ts = int((now or datetime.now(tz=UTC)).timestamp())
    if current_ts > payload.exp:
        raise ValueError("token is expired")

    return payload


def build_candidate_apply_link(*, settings: TelegramLinkSettings, token: str) -> str:
    query = urlencode({"token": token})
    return f"{settings.public_web_base_url}/candidate/apply?{query}"


def _b64url_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _b64url_decode(raw: str) -> bytes:
    padding = "=" * (-len(raw) % 4)
    return base64.urlsafe_b64decode(f"{raw}{padding}")


def _as_optional_str(value: object) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    return str(value)
