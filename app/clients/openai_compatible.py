import json
import time
import httpx
from typing import Optional

from app.domain.dto import LLMClientRequest, LLMClientResult
from app.domain.contracts import LLMClient
import logging
logger = logging.getLogger(__name__)


class OpenAICompatibleClient(LLMClient):
    """
    LLM client for OpenAI-compatible API.
    base_url, api_key are set via env.
    """

    def __init__(
        self,
        api_key: str,
        base_url: str = "https://api.deepseek.com/v1/chat/completions",
        timeout: float = 60.0,
    ):
        self.api_key = api_key
        self.base_url = base_url
        self.timeout = timeout
        self._client = httpx.AsyncClient(timeout=timeout)

    async def evaluate(self, request: LLMClientRequest) -> LLMClientResult:
        messages = [
            {"role": "system", "content": request.system_prompt},
            {"role": "user", "content": request.user_prompt},
        ]

        payload = {
            "model": request.model,
            "messages": messages,
            "temperature": request.temperature,
            "seed": request.seed,
        }

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        start_time = time.monotonic()
        try:
            response = await self._client.post(self.base_url, json=payload, headers=headers)
            response.raise_for_status()
            data = response.json()
            logger.info(f"API response received, status {response.status_code}")
            logger.debug(f"Response body: {response.text[:500]}")
        # except httpx.HTTPStatusError as e:
        #     raise Exception(f"API error: {e.response.text}") from e
        except httpx.HTTPStatusError as e:
            error_text = e.response.text
            logger.error(f"HTTP error {e.response.status_code}: {error_text}")
            raise Exception(f"API error: {error_text}") from e
        except Exception as e:
            raise Exception(f"Request failed: {str(e)}") from e
        finally:
            latency_ms = int((time.monotonic() - start_time) * 1000)

        choices = data.get("choices", [])
        if not choices:
            raise Exception("No choices in API response")
        content = choices[0].get("message", {}).get("content", "")

        usage = data.get("usage", {})
        tokens_input = usage.get("prompt_tokens", 0)
        tokens_output = usage.get("completion_tokens", 0)

        raw_json = None
        try:
            raw_json = json.loads(content)
        except json.JSONDecodeError:
            pass

        return LLMClientResult(
            raw_text=content,
            raw_json=raw_json,
            tokens_input=tokens_input,
            tokens_output=tokens_output,
            latency_ms=latency_ms,
        )

    async def close(self):
        await self._client.aclose()