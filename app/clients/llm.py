from __future__ import annotations

from dataclasses import dataclass
import json
from socket import timeout as socket_timeout
from time import perf_counter, sleep
from urllib import error as urllib_error
from urllib import request as urllib_request

from app.domain.dto import LLMClientRequest, LLMClientResult

DEFAULT_OPENAI_COMPATIBLE_CHAT_COMPLETIONS_PATH = "/chat/completions"


class LLMAdapterError(RuntimeError):
    pass


class LLMRetryableError(LLMAdapterError):
    pass


@dataclass(frozen=True)
class OpenAICompatibleLLMClient:
    api_key: str
    base_url: str
    model: str
    request_timeout_seconds: float = 30.0
    request_max_retries: int = 2
    request_retry_backoff_ms: int = 1000
    chat_completions_path: str = DEFAULT_OPENAI_COMPATIBLE_CHAT_COMPLETIONS_PATH

    def evaluate(self, request: LLMClientRequest) -> LLMClientResult:
        payload: dict[str, object] = {
            "model": request.model or self.model,
            "messages": [
                {"role": "system", "content": request.system_prompt},
                {"role": "user", "content": request.user_prompt},
            ],
            "temperature": request.temperature,
            "response_format": {"type": "json_object"},
        }
        if request.seed is not None:
            payload["seed"] = request.seed

        start = perf_counter()
        response_json = self._request_json(payload=payload)
        latency_ms = int((perf_counter() - start) * 1000)

        if not isinstance(response_json, dict):
            raise ValueError("llm response must be a JSON object")

        choices = response_json.get("choices")
        if not isinstance(choices, list) or not choices:
            raise ValueError("llm response must include non-empty choices")

        first_choice = choices[0]
        if not isinstance(first_choice, dict):
            raise ValueError("llm response choice must be an object")

        message = first_choice.get("message")
        if not isinstance(message, dict):
            raise ValueError("llm response choice.message must be an object")

        content = message.get("content")
        if not isinstance(content, str) or not content.strip():
            raise ValueError("llm response choice.message.content must be a non-empty string")

        usage = response_json.get("usage")
        tokens_input = 0
        tokens_output = 0
        if isinstance(usage, dict):
            prompt_tokens = usage.get("prompt_tokens")
            completion_tokens = usage.get("completion_tokens")
            if isinstance(prompt_tokens, int) and prompt_tokens >= 0:
                tokens_input = prompt_tokens
            if isinstance(completion_tokens, int) and completion_tokens >= 0:
                tokens_output = completion_tokens

        raw_json: dict[str, object] | None = None
        try:
            parsed = json.loads(content)
            if isinstance(parsed, dict):
                raw_json = parsed
        except json.JSONDecodeError:
            raw_json = None

        return LLMClientResult(
            raw_text=content,
            raw_json=raw_json,
            tokens_input=tokens_input,
            tokens_output=tokens_output,
            latency_ms=latency_ms,
        )

    def _request_json(self, *, payload: dict[str, object]) -> object:
        url = f"{self.base_url.rstrip('/')}{self.chat_completions_path}"
        request = urllib_request.Request(
            url=url,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
            },
            method="POST",
        )
        attempts_total = max(self.request_max_retries, 0) + 1
        last_retryable_error: LLMRetryableError | None = None

        for attempt in range(attempts_total):
            body_text = ""
            try:
                with urllib_request.urlopen(request, timeout=self.request_timeout_seconds) as response:
                    body_text = response.read().decode("utf-8")
            except urllib_error.HTTPError as exc:
                error_body = exc.read().decode("utf-8", errors="replace")
                if exc.code >= 500 or exc.code == 429:
                    last_retryable_error = LLMRetryableError(error_body or f"llm HTTP error: {exc.code}")
                else:
                    raise LLMAdapterError(error_body or f"llm HTTP error: {exc.code}") from exc
            except (urllib_error.URLError, TimeoutError, socket_timeout) as exc:
                last_retryable_error = LLMRetryableError(str(exc))
            else:
                try:
                    return json.loads(body_text)
                except json.JSONDecodeError:
                    last_retryable_error = LLMRetryableError("llm API returned invalid JSON")

            if attempt < attempts_total - 1:
                sleep(self._retry_delay_seconds(attempt))
                continue

        raise last_retryable_error or LLMRetryableError("llm request failed")

    def _retry_delay_seconds(self, attempt: int) -> float:
        base = self.request_retry_backoff_ms / 1000
        return base * (2**attempt)
