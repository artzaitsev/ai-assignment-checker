from __future__ import annotations

from email.message import Message
from io import BytesIO
from urllib import error as urllib_error

import pytest

from app.clients.llm import LLMAdapterError, LLMRetryableError, OpenAICompatibleLLMClient
from app.domain.dto import LLMClientRequest


@pytest.mark.unit
def test_evaluate_maps_openai_compatible_response(monkeypatch: pytest.MonkeyPatch) -> None:
    client = OpenAICompatibleLLMClient(
        api_key="test-key",
        base_url="https://agent.timeweb.cloud/v1",
        model="gpt-test",
        request_max_retries=0,
    )

    def _fake_request_json(self: OpenAICompatibleLLMClient, *, payload: dict[str, object]) -> object:
        del self
        assert payload["model"] == "gpt-test"
        assert payload["temperature"] == 0.2
        assert payload["seed"] == 42
        return {
            "choices": [
                {
                    "message": {
                        "content": '{"criteria":[],"organizer_feedback":{},"candidate_feedback":{},"ai_assistance":{"likelihood":0.1,"confidence":0.2}}'
                    }
                }
            ],
            "usage": {
                "prompt_tokens": 123,
                "completion_tokens": 45,
            },
        }

    monkeypatch.setattr(OpenAICompatibleLLMClient, "_request_json", _fake_request_json)

    result = client.evaluate(
        LLMClientRequest(
            system_prompt="system",
            user_prompt="user",
            model="gpt-test",
            temperature=0.2,
            seed=42,
            response_language="en",
        )
    )

    assert result.raw_json is not None
    assert result.raw_json["criteria"] == []
    assert result.tokens_input == 123
    assert result.tokens_output == 45
    assert result.latency_ms >= 0


@pytest.mark.unit
def test_evaluate_maps_missing_content_to_schema_error(monkeypatch: pytest.MonkeyPatch) -> None:
    client = OpenAICompatibleLLMClient(
        api_key="test-key",
        base_url="https://agent.timeweb.cloud/v1",
        model="gpt-test",
        request_max_retries=0,
    )

    def _fake_request_json(self: OpenAICompatibleLLMClient, *, payload: dict[str, object]) -> object:
        del self, payload
        return {"choices": [{"message": {"content": ""}}]}

    monkeypatch.setattr(OpenAICompatibleLLMClient, "_request_json", _fake_request_json)

    with pytest.raises(ValueError, match="non-empty string"):
        client.evaluate(
            LLMClientRequest(
                system_prompt="system",
                user_prompt="user",
                model="gpt-test",
                temperature=0.2,
                seed=None,
                response_language="en",
            )
        )


@pytest.mark.unit
def test_request_json_maps_timeout_to_retryable(monkeypatch: pytest.MonkeyPatch) -> None:
    client = OpenAICompatibleLLMClient(
        api_key="test-key",
        base_url="https://agent.timeweb.cloud/v1",
        model="gpt-test",
    )

    def _fake_urlopen(*args: object, **kwargs: object) -> object:
        del args, kwargs
        raise TimeoutError("timed out")

    monkeypatch.setattr("app.clients.llm.urllib_request.urlopen", _fake_urlopen)

    with pytest.raises(LLMRetryableError, match="timed out"):
        client._request_json(payload={"model": "gpt-test"})


@pytest.mark.unit
def test_request_json_maps_400_to_non_retryable(monkeypatch: pytest.MonkeyPatch) -> None:
    client = OpenAICompatibleLLMClient(
        api_key="test-key",
        base_url="https://agent.timeweb.cloud/v1",
        model="gpt-test",
    )

    def _fake_urlopen(*args: object, **kwargs: object) -> object:
        del args, kwargs
        raise urllib_error.HTTPError(
            url="https://agent.timeweb.cloud/v1/chat/completions",
            code=400,
            msg="Bad Request",
            hdrs=Message(),
            fp=BytesIO(b'{"error":"bad request"}'),
        )

    monkeypatch.setattr("app.clients.llm.urllib_request.urlopen", _fake_urlopen)

    with pytest.raises(LLMAdapterError, match="bad request"):
        client._request_json(payload={"model": "gpt-test"})


@pytest.mark.unit
def test_request_json_retries_on_timeout_and_succeeds(monkeypatch: pytest.MonkeyPatch) -> None:
    client = OpenAICompatibleLLMClient(
        api_key="test-key",
        base_url="https://agent.timeweb.cloud/v1",
        model="gpt-test",
        request_timeout_seconds=1,
        request_max_retries=2,
        request_retry_backoff_ms=10,
    )

    calls = {"count": 0}

    class _FakeResponse:
        def __enter__(self) -> "_FakeResponse":
            return self

        def __exit__(self, exc_type, exc, tb) -> bool:
            del exc_type, exc, tb
            return False

        def read(self) -> bytes:
            return b'{"choices":[{"message":{"content":"{}"}}]}'

    def _fake_urlopen(*args: object, **kwargs: object) -> object:
        del args, kwargs
        calls["count"] += 1
        if calls["count"] < 3:
            raise TimeoutError("timed out")
        return _FakeResponse()

    def _fake_sleep(seconds: float) -> None:
        del seconds

    monkeypatch.setattr("app.clients.llm.urllib_request.urlopen", _fake_urlopen)
    monkeypatch.setattr("app.clients.llm.sleep", _fake_sleep)

    response = client._request_json(payload={"model": "gpt-test"})
    assert isinstance(response, dict)
    assert calls["count"] == 3
