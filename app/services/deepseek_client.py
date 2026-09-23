"""Compatibility exports for older DeepSeek integrations."""

import httpx  # Preserve the historical patch target used by callers/tests.

from app.services.model_client import (
    DEFAULT_DEEPSEEK_API_URL,
    DEFAULT_DEEPSEEK_MODEL,
    DEFAULT_MAX_TOKENS,
    MAX_JSON_ATTEMPTS,
    LLMJSONClient,
)


class DeepSeekJSONClient(LLMJSONClient):
    def __init__(self, *args, **kwargs):
        kwargs["provider"] = "deepseek"
        super().__init__(*args, **kwargs)
