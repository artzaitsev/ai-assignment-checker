from __future__ import annotations

import pytest

from app.clients.telegram import RealTelegramClient, TelegramNonRetryableError, TelegramRetryableError


@pytest.mark.unit
def test_poll_events_maps_typed_command_event(monkeypatch: pytest.MonkeyPatch) -> None:
    client = RealTelegramClient(bot_token="test-token")

    def _fake_request_json(self: RealTelegramClient, *, url: str, payload: dict[str, object]) -> object:
        del self
        del url
        assert payload == {"timeout": 30}
        return {
            "ok": True,
            "result": [
                {
                    "update_id": 1001,
                    "message": {
                        "chat": {"id": 2001},
                        "from": {"id": 3001},
                        "text": "/start asg-1",
                        "entities": [{"type": "bot_command", "offset": 0, "length": 6}],
                    },
                }
            ],
        }

    monkeypatch.setattr(RealTelegramClient, "_request_json", _fake_request_json)

    events = client.poll_events()

    assert len(events) == 1
    event = events[0]
    assert event.update_id == "1001"
    assert event.chat_id == "2001"
    assert event.telegram_user_id == "3001"
    assert event.kind == "command"
    assert event.command == "/start"
    assert event.text == "/start asg-1"


@pytest.mark.unit
def test_poll_events_skips_unsupported_payload_variants(monkeypatch: pytest.MonkeyPatch) -> None:
    client = RealTelegramClient(bot_token="test-token")

    def _fake_request_json(self: RealTelegramClient, *, url: str, payload: dict[str, object]) -> object:
        del self
        del url, payload
        return {
            "ok": True,
            "result": [
                {"update_id": 1, "message": {"chat": {"id": 10}}},
                {
                    "update_id": 2,
                    "message": {
                        "chat": {"id": 11},
                        "from": {"id": 21},
                        "text": "hello",
                    },
                },
            ],
        }

    monkeypatch.setattr(RealTelegramClient, "_request_json", _fake_request_json)

    events = client.poll_events()

    assert len(events) == 1
    assert events[0].update_id == "2"
    assert events[0].kind == "text"
    assert events[0].command is None


@pytest.mark.unit
def test_poll_events_sends_numeric_offset(monkeypatch: pytest.MonkeyPatch) -> None:
    client = RealTelegramClient(bot_token="test-token")
    seen_payload: dict[str, object] = {}

    def _fake_request_json(self: RealTelegramClient, *, url: str, payload: dict[str, object]) -> object:
        del self
        del url
        seen_payload.update(payload)
        return {"ok": True, "result": []}

    monkeypatch.setattr(RealTelegramClient, "_request_json", _fake_request_json)

    events = client.poll_events(offset="42")

    assert events == []
    assert seen_payload["offset"] == 42


@pytest.mark.unit
def test_send_text_returns_external_message_id(monkeypatch: pytest.MonkeyPatch) -> None:
    client = RealTelegramClient(bot_token="test-token")
    seen_payload: dict[str, object] = {}

    def _fake_request_json(self: RealTelegramClient, *, url: str, payload: dict[str, object]) -> object:
        del self
        del url
        seen_payload.update(payload)
        assert payload["chat_id"] == "chat-1"
        assert payload["text"] == "Hello"
        return {
            "ok": True,
            "result": {
                "message_id": 555,
            },
        }

    monkeypatch.setattr(RealTelegramClient, "_request_json", _fake_request_json)

    result = client.send_text(chat_id="chat-1", message="Hello")
    assert result == "555"
    assert "parse_mode" not in seen_payload


@pytest.mark.unit
def test_send_text_uses_html_parse_mode_for_link_markup(monkeypatch: pytest.MonkeyPatch) -> None:
    client = RealTelegramClient(bot_token="test-token")
    seen_payload: dict[str, object] = {}

    def _fake_request_json(self: RealTelegramClient, *, url: str, payload: dict[str, object]) -> object:
        del self, url
        seen_payload.update(payload)
        return {"ok": True, "result": {"message_id": 123}}

    monkeypatch.setattr(RealTelegramClient, "_request_json", _fake_request_json)

    message_id = client.send_text(
        chat_id="chat-1",
        message='Начните здесь: <a href="https://example.com">Открыть форму</a>',
    )

    assert message_id == "123"
    assert seen_payload.get("parse_mode") == "HTML"


@pytest.mark.unit
def test_post_api_maps_retryable_telegram_error(monkeypatch: pytest.MonkeyPatch) -> None:
    client = RealTelegramClient(bot_token="test-token")

    def _fake_request_json(self: RealTelegramClient, *, url: str, payload: dict[str, object]) -> object:
        del self
        del url, payload
        return {"ok": False, "error_code": 502, "description": "Bad gateway"}

    monkeypatch.setattr(RealTelegramClient, "_request_json", _fake_request_json)

    with pytest.raises(TelegramRetryableError, match="Bad gateway"):
        client.poll_events()


@pytest.mark.unit
def test_post_api_maps_non_retryable_telegram_error(monkeypatch: pytest.MonkeyPatch) -> None:
    client = RealTelegramClient(bot_token="test-token")

    def _fake_request_json(self: RealTelegramClient, *, url: str, payload: dict[str, object]) -> object:
        del self
        del url, payload
        return {"ok": False, "error_code": 401, "description": "Unauthorized"}

    monkeypatch.setattr(RealTelegramClient, "_request_json", _fake_request_json)

    with pytest.raises(TelegramNonRetryableError, match="Unauthorized"):
        client.poll_events()


@pytest.mark.unit
def test_poll_events_rejects_non_numeric_offset() -> None:
    client = RealTelegramClient(bot_token="test-token")

    with pytest.raises(TelegramNonRetryableError, match="offset must be numeric"):
        client.poll_events(offset="not-a-number")
