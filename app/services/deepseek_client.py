import os

import httpx

from app.services.model_json import (
    extract_chat_message_content,
    parse_model_json_response,
)


DEFAULT_DEEPSEEK_API_URL = "https://api.deepseek.com/chat/completions"
DEFAULT_DEEPSEEK_MODEL = "deepseek-v4-flash"
DEFAULT_MAX_TOKENS = 8192


class DeepSeekJSONClient:
    def __init__(
        self,
        api_key: str | None = None,
        model: str | None = None,
        api_url: str | None = None,
        timeout_seconds: float = 120,
        max_tokens: int | None = None,
    ):
        self.api_key = api_key or os.getenv("DEEPSEEK_API_KEY")
        self.model = model or os.getenv(
            "DEEPSEEK_MODEL",
            DEFAULT_DEEPSEEK_MODEL,
        )
        self.api_url = api_url or os.getenv(
            "DEEPSEEK_API_URL",
            DEFAULT_DEEPSEEK_API_URL,
        )
        self.timeout_seconds = timeout_seconds
        self.max_tokens = (
            max_tokens
            if max_tokens is not None
            else _read_max_tokens()
        )

        if not self.api_key:
            raise RuntimeError("DEEPSEEK_API_KEY is not set.")
        if self.max_tokens < 1:
            raise RuntimeError("max_tokens must be greater than zero.")

    def request_json(self, system_prompt: str, user_prompt: str) -> dict:
        try:
            response = httpx.post(
                self.api_url,
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": self.model,
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt},
                    ],
                    "response_format": {"type": "json_object"},
                    "thinking": {"type": "disabled"},
                    "temperature": 0.1,
                    "max_tokens": self.max_tokens,
                    "stream": False,
                },
                timeout=self.timeout_seconds,
            )
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise RuntimeError(
                f"DeepSeek API request failed with status "
                f"{exc.response.status_code}: {exc.response.text}"
            ) from exc
        except httpx.RequestError as exc:
            raise RuntimeError(f"DeepSeek API request failed: {exc}") from exc

        try:
            response_data = response.json()
        except ValueError as exc:
            raise ValueError("DeepSeek API response is not valid JSON.") from exc

        content = extract_chat_message_content(response_data, "DeepSeek")
        return parse_model_json_response(content)


def _read_max_tokens() -> int:
    raw_value = os.getenv("DEEPSEEK_MAX_TOKENS")
    if raw_value is None:
        return DEFAULT_MAX_TOKENS

    try:
        max_tokens = int(raw_value)
    except ValueError as exc:
        raise RuntimeError("DEEPSEEK_MAX_TOKENS must be an integer.") from exc

    if max_tokens < 1:
        raise RuntimeError("DEEPSEEK_MAX_TOKENS must be greater than zero.")
    return max_tokens
