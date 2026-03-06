from __future__ import annotations

from dataclasses import dataclass
import json
from socket import timeout as socket_timeout
from urllib import error as urllib_error
from urllib import request as urllib_request

from app.domain.models import TelegramInboundEvent

DEFAULT_TELEGRAM_API_BASE_URL = "https://api.telegram.org"


class TelegramAdapterError(RuntimeError):
    pass


class TelegramRetryableError(TelegramAdapterError):
    pass


class TelegramNonRetryableError(TelegramAdapterError):
    pass


@dataclass(frozen=True)
class RealTelegramClient:
    bot_token: str
    api_base_url: str = DEFAULT_TELEGRAM_API_BASE_URL
    request_timeout_seconds: float = 35.0

    def poll_events(self, *, timeout: int = 30, offset: str | None = None) -> list[TelegramInboundEvent]:
        payload: dict[str, object] = {"timeout": timeout}
        if offset is not None:
            payload["offset"] = _parse_offset(offset)

        result = self._post_api("getUpdates", payload)
        if not isinstance(result, list):
            raise TelegramNonRetryableError("telegram getUpdates result must be a list")

        events: list[TelegramInboundEvent] = []
        for update in result:
            event = _map_update(update)
            if event is not None:
                events.append(event)
        return events

    def send_text(self, *, chat_id: str, message: str) -> str | None:
        payload: dict[str, object] = {
            "chat_id": chat_id,
            "text": message,
        }
        if "<a " in message and "</a>" in message:
            payload["parse_mode"] = "HTML"

        result = self._post_api("sendMessage", payload)
        if not isinstance(result, dict):
            raise TelegramNonRetryableError("telegram sendMessage result must be an object")

        message_id = result.get("message_id")
        if message_id is None:
            return None
        return str(message_id)

    def _post_api(self, method: str, payload: dict[str, object]) -> object:
        url = f"{self.api_base_url}/bot{self.bot_token}/{method}"
        response_json = self._request_json(url=url, payload=payload)

        if not isinstance(response_json, dict):
            raise TelegramNonRetryableError("telegram API response must be a JSON object")

        if response_json.get("ok") is not True:
            description = str(response_json.get("description", "telegram API request failed"))
            error_code = response_json.get("error_code")
            if _is_retryable_error_code(error_code):
                raise TelegramRetryableError(description)
            raise TelegramNonRetryableError(description)

        return response_json.get("result")

    def _request_json(self, *, url: str, payload: dict[str, object]) -> object:
        request = urllib_request.Request(
            url=url,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib_request.urlopen(request, timeout=self.request_timeout_seconds) as response:
                body = response.read().decode("utf-8")
        except urllib_error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            if exc.code >= 500 or exc.code == 429:
                raise TelegramRetryableError(body or f"telegram HTTP error: {exc.code}") from exc
            raise TelegramNonRetryableError(body or f"telegram HTTP error: {exc.code}") from exc
        except (urllib_error.URLError, TimeoutError, socket_timeout) as exc:
            raise TelegramRetryableError(str(exc)) from exc

        try:
            return json.loads(body)
        except json.JSONDecodeError as exc:
            raise TelegramNonRetryableError("telegram API returned invalid JSON") from exc


def _is_retryable_error_code(error_code: object) -> bool:
    if not isinstance(error_code, int):
        return False
    return error_code >= 500 or error_code == 429


def _parse_offset(offset: str) -> int:
    try:
        parsed = int(offset)
    except ValueError as exc:
        raise TelegramNonRetryableError(f"telegram offset must be numeric, got: {offset!r}") from exc
    if parsed < 0:
        raise TelegramNonRetryableError("telegram offset must be non-negative")
    return parsed


def _map_update(update: object) -> TelegramInboundEvent | None:
    if not isinstance(update, dict):
        return None
    update_id = update.get("update_id")
    if update_id is None:
        return None

    message_payload = update.get("message")
    if not isinstance(message_payload, dict):
        return None

    chat_payload = message_payload.get("chat")
    from_payload = message_payload.get("from")
    if not isinstance(chat_payload, dict) or not isinstance(from_payload, dict):
        return None

    chat_id = chat_payload.get("id")
    telegram_user_id = from_payload.get("id")
    if chat_id is None or telegram_user_id is None:
        return None

    text_raw = message_payload.get("text")
    text = text_raw if isinstance(text_raw, str) else None
    command = _extract_command(message_payload=message_payload, text=text)
    kind = "command" if command is not None else "text"

    return TelegramInboundEvent(
        update_id=str(update_id),
        chat_id=str(chat_id),
        telegram_user_id=str(telegram_user_id),
        kind=kind,
        command=command,
        text=text,
    )


def _extract_command(*, message_payload: dict[str, object], text: str | None) -> str | None:
    if text is None:
        return None

    entities = message_payload.get("entities")
    if isinstance(entities, list):
        for entity in entities:
            if not isinstance(entity, dict):
                continue
            if entity.get("type") != "bot_command":
                continue
            offset_raw = entity.get("offset")
            length_raw = entity.get("length")
            if offset_raw != 0 or not isinstance(length_raw, int) or length_raw <= 0:
                continue
            return text[:length_raw].split(maxsplit=1)[0].strip() or None

    normalized = text.strip()
    if not normalized.startswith("/"):
        return None
    return normalized.split(maxsplit=1)[0]
