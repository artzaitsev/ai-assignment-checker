from __future__ import annotations

from os import environ as os_environ
from urllib.parse import urlparse

from app.domain.models import ApplySessionSettings, TelegramLinkSettings

def telegram_link_settings_from_env() -> TelegramLinkSettings:
    base_url = os_environ.get("PUBLIC_WEB_BASE_URL", "http://localhost:8000").strip().rstrip("/")
    parsed = urlparse(base_url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("PUBLIC_WEB_BASE_URL must be an absolute http(s) URL")

    signing_secret = os_environ.get("TELEGRAM_LINK_SIGNING_SECRET", "dev-telegram-link-secret").strip()
    if len(signing_secret) < 12:
        raise ValueError("TELEGRAM_LINK_SIGNING_SECRET must be at least 12 characters")

    ttl_raw = os_environ.get("TELEGRAM_LINK_TTL_SECONDS", "900").strip()
    try:
        ttl_seconds = int(ttl_raw)
    except ValueError as exc:
        raise ValueError("TELEGRAM_LINK_TTL_SECONDS must be an integer") from exc
    if ttl_seconds <= 0:
        raise ValueError("TELEGRAM_LINK_TTL_SECONDS must be > 0")

    return TelegramLinkSettings(
        public_web_base_url=base_url,
        signing_secret=signing_secret,
        ttl_seconds=ttl_seconds,
    )


def apply_session_settings_from_env() -> ApplySessionSettings:
    signing_secret = os_environ.get("APPLY_SESSION_SIGNING_SECRET", "dev-apply-session-secret").strip()
    if len(signing_secret) < 12:
        raise ValueError("APPLY_SESSION_SIGNING_SECRET must be at least 12 characters")

    ttl_raw = os_environ.get("APPLY_SESSION_TTL_SECONDS", "900").strip()
    try:
        ttl_seconds = int(ttl_raw)
    except ValueError as exc:
        raise ValueError("APPLY_SESSION_TTL_SECONDS must be an integer") from exc
    if ttl_seconds <= 0:
        raise ValueError("APPLY_SESSION_TTL_SECONDS must be > 0")

    return ApplySessionSettings(
        signing_secret=signing_secret,
        ttl_seconds=ttl_seconds,
    )
