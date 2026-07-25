import json
import re


def parse_model_json_response(response_text: str) -> dict:
    response_text = _extract_json_text(response_text)
    try:
        return json.loads(response_text)
    except json.JSONDecodeError as exc:
        raise ValueError("Model response is not valid JSON.") from exc


def extract_chat_message_content(response_data: dict, provider_name: str) -> str:
    try:
        content = response_data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise ValueError(
            f"Unexpected {provider_name} response shape: {response_data}"
        ) from exc

    if not isinstance(content, str) or not content.strip():
        raise ValueError(f"{provider_name} returned empty message content.")
    return content


def _extract_json_text(text: str) -> str:
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()
    fenced_match = re.search(r"```(?:json)?\s*(.*?)\s*```", text, flags=re.DOTALL)
    if fenced_match:
        text = fenced_match.group(1).strip()

    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end < start:
        return text

    return text[start : end + 1]
