"""Shared JSON client for Chat Completions, Claude Messages and Gemini."""

import json
import math
import os
from urllib.parse import quote

import httpx

from app.services.model_json import extract_chat_message_content, parse_model_json_response


DEFAULT_DEEPSEEK_API_URL = "https://api.deepseek.com/chat/completions"
DEFAULT_DEEPSEEK_MODEL = "deepseek-v4-flash"
DEFAULT_MAX_TOKENS = 16384
MAX_JSON_ATTEMPTS = 2


class LLMJSONClient:
    def __init__(
        self,
        api_key: str | None = None,
        model: str | None = None,
        api_url: str | None = None,
        timeout_seconds: float = 120,
        max_tokens: int | None = None,
        *,
        provider: str | None = None,
    ):
        self.provider = provider or os.getenv("LLM_PROVIDER", "deepseek")
        if self.provider not in {"deepseek", "openai_compatible", "anthropic", "gemini"}:
            raise RuntimeError("LLM_PROVIDER must be deepseek, openai_compatible, anthropic or gemini.")
        legacy = self.provider == "deepseek"
        self.provider_name = {"deepseek": "DeepSeek", "anthropic": "Claude", "gemini": "Gemini"}.get(self.provider, "LLM")

        def setting(name: str, default: str | None = None) -> str | None:
            value = os.getenv(f"LLM_{name}")
            prefix = {"deepseek": "DEEPSEEK", "anthropic": "ANTHROPIC", "gemini": "GEMINI"}.get(self.provider)
            if value is None and prefix:
                value = os.getenv(f"{prefix}_{name}")
            return default if value is None else value

        self.api_key = api_key if api_key is not None else setting("API_KEY")
        self.model = model or setting("MODEL", DEFAULT_DEEPSEEK_MODEL if legacy else None)
        default_url = {
            "deepseek": DEFAULT_DEEPSEEK_API_URL,
            "anthropic": "https://api.anthropic.com/v1/messages",
            "gemini": "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
        }.get(self.provider)
        self.api_url = api_url or setting("API_URL", default_url)
        if self.provider != "openai_compatible" and not self.api_key:
            prefix = {"deepseek": "DEEPSEEK", "anthropic": "ANTHROPIC", "gemini": "GEMINI"}[self.provider]
            raise RuntimeError(f"LLM_API_KEY (or {prefix}_API_KEY) is not set.")
        if not self.model or not self.api_url:
            raise RuntimeError("LLM_MODEL and LLM_API_URL must be set unless the provider has defaults.")
        if self.provider == "gemini":
            self.api_url = self.api_url.replace("{model}", quote(self.model.removeprefix("models/"), safe=""))
        try:
            self.max_tokens = int(max_tokens if max_tokens is not None else setting("MAX_TOKENS", str(DEFAULT_MAX_TOKENS)))
        except ValueError as exc:
            raise RuntimeError("LLM_MAX_TOKENS must be an integer.") from exc
        if self.max_tokens < 1:
            raise RuntimeError("max_tokens must be greater than zero.")
        self.timeout_seconds = timeout_seconds
        self.json_mode = os.getenv("LLM_JSON_MODE", "true").lower()
        if self.json_mode not in {"true", "false"}:
            raise RuntimeError("LLM_JSON_MODE must be true or false.")
        self.token_parameter = os.getenv("LLM_TOKEN_PARAMETER", "max_tokens")
        if self.token_parameter not in {"max_tokens", "max_completion_tokens"}:
            raise RuntimeError("LLM_TOKEN_PARAMETER must be max_tokens or max_completion_tokens.")
        temperature = os.getenv("LLM_TEMPERATURE", "0.1")
        try:
            self.temperature = None if temperature.lower() == "omit" else float(temperature)
        except ValueError as exc:
            raise RuntimeError("LLM_TEMPERATURE must be a number or omit.") from exc
        if self.temperature is not None and (not math.isfinite(self.temperature) or not 0 <= self.temperature <= 2):
            raise RuntimeError("LLM_TEMPERATURE must be between 0 and 2, or omit.")
        raw_extra = os.getenv("LLM_EXTRA_BODY")
        try:
            self.extra_body = json.loads(raw_extra) if raw_extra is not None else (
                {"thinking": {"type": "disabled"}} if legacy else {}
            )
        except ValueError as exc:
            raise RuntimeError("LLM_EXTRA_BODY must be a JSON object.") from exc
        if not isinstance(self.extra_body, dict):
            raise RuntimeError("LLM_EXTRA_BODY must be a JSON object.")
        reserved = {"model", "messages", "system", "contents", "systemInstruction", "stream", "response_format", "temperature", "max_tokens", "max_completion_tokens"}
        if reserved.intersection(self.extra_body):
            raise RuntimeError("LLM_EXTRA_BODY cannot override standard request fields; use the named LLM settings.")
        if self.provider == "gemini":
            generation = self.extra_body.get("generationConfig", {})
            if not isinstance(generation, dict) or {"temperature", "maxOutputTokens", "responseMimeType"}.intersection(generation):
                raise RuntimeError("generationConfig must be an object without temperature, maxOutputTokens or responseMimeType; use the named LLM settings.")

    def request_json(self, system_prompt: str, user_prompt: str) -> dict:
        last_error: ValueError | None = None
        saw_truncated_response = False
        for attempt in range(MAX_JSON_ATTEMPTS):
            response_data = self._request_response_data(system_prompt, user_prompt, attempt)
            truncated = self._is_truncated(response_data)
            saw_truncated_response |= truncated
            try:
                if truncated:
                    raise ValueError("Model response was truncated.")
                content = self._extract_content(response_data)
                result = parse_model_json_response(content)
                if not isinstance(result, dict):
                    raise ValueError("Model response must be a JSON object.")
                return result
            except ValueError as exc:
                last_error = exc

        if saw_truncated_response:
            raise ValueError(
                f"{self.provider_name} 响应达到 token 上限 ({self.max_tokens}) 后被截断，"
                f"共尝试 {MAX_JSON_ATTEMPTS} 次。"
            ) from last_error
        raise ValueError(
            f"{self.provider_name} 连续 {MAX_JSON_ATTEMPTS} 次返回无法解析的 JSON。"
        ) from last_error

    def _request_response_data(self, system_prompt: str, user_prompt: str, attempt: int) -> dict:
        body, headers = self._build_request(system_prompt, user_prompt, attempt)
        try:
            response = httpx.post(self.api_url, headers=headers, json=body, timeout=self.timeout_seconds)
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise RuntimeError(
                f"{self.provider_name} API request failed with status {exc.response.status_code}."
            ) from exc
        except httpx.RequestError as exc:
            raise RuntimeError(f"{self.provider_name} API request failed ({type(exc).__name__}).") from exc
        try:
            response_data = response.json()
        except ValueError as exc:
            raise ValueError(f"{self.provider_name} API response is not valid JSON.") from exc
        if not isinstance(response_data, dict):
            raise ValueError(f"{self.provider_name} API response must be a JSON object.")
        return response_data

    def _build_request(self, system_prompt: str, user_prompt: str, attempt: int) -> tuple[dict, dict]:
        system_prompt += "\nReturn only a valid JSON object. Do not include explanations."
        headers = {"Content-Type": "application/json"}
        temperature = None if self.temperature is None else min(2, self.temperature + attempt * 0.1)
        if self.provider == "anthropic":
            headers.update({"x-api-key": self.api_key, "anthropic-version": "2023-06-01"})
            body = {
                **self.extra_body,
                "model": self.model,
                "system": system_prompt,
                "messages": [{"role": "user", "content": user_prompt}],
                "max_tokens": self.max_tokens,
                "stream": False,
            }
            if temperature is not None:
                body["temperature"] = min(1, temperature)
            return body, headers
        if self.provider == "gemini":
            headers["x-goog-api-key"] = self.api_key
            generation = {**self.extra_body.get("generationConfig", {}), "maxOutputTokens": self.max_tokens}
            if self.json_mode == "true":
                generation["responseMimeType"] = "application/json"
            if temperature is not None:
                generation["temperature"] = temperature
            return {
                **self.extra_body,
                "systemInstruction": {"parts": [{"text": system_prompt}]},
                "contents": [{"role": "user", "parts": [{"text": user_prompt}]}],
                "generationConfig": generation,
            }, headers
        body = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            self.token_parameter: self.max_tokens,
            "stream": False,
            **self.extra_body,
        }
        if self.json_mode == "true":
            body["response_format"] = {"type": "json_object"}
        if temperature is not None:
            body["temperature"] = temperature
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return body, headers

    def _is_truncated(self, data: dict) -> bool:
        if self.provider == "anthropic":
            return data.get("stop_reason") == "max_tokens"
        if self.provider == "gemini":
            try:
                return data["candidates"][0].get("finishReason") == "MAX_TOKENS"
            except (KeyError, IndexError, TypeError, AttributeError):
                return False
        return _finish_reason(data) == "length"

    def _extract_content(self, data: dict) -> str:
        if self.provider in {"deepseek", "openai_compatible"}:
            return extract_chat_message_content(data, self.provider_name)
        try:
            if self.provider == "anthropic":
                if data.get("stop_reason") not in {"end_turn", "stop_sequence"}:
                    raise ValueError("Claude did not complete a text response.")
                blocks = data["content"]
                parts = [block["text"] for block in blocks if block.get("type") == "text"]
            else:
                candidate = data["candidates"][0]
                if candidate.get("finishReason") != "STOP":
                    raise ValueError("Gemini did not complete a text response (possibly blocked).")
                parts = [part["text"] for part in candidate["content"]["parts"]
                         if "text" in part and not part.get("thought")]
            content = "".join(parts)
        except (KeyError, IndexError, TypeError, AttributeError) as exc:
            raise ValueError(f"Unexpected {self.provider_name} response shape.") from exc
        if not content.strip():
            raise ValueError(f"{self.provider_name} returned empty message content.")
        return content


def _finish_reason(response_data: dict) -> str | None:
    try:
        finish_reason = response_data["choices"][0].get("finish_reason")
    except (KeyError, IndexError, TypeError, AttributeError):
        return None
    return finish_reason if isinstance(finish_reason, str) else None
