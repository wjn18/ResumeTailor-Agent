import os
import unittest
from unittest.mock import Mock, patch

from app.services.deepseek_client import (
    DEFAULT_DEEPSEEK_API_URL,
    DEFAULT_MAX_TOKENS,
    DeepSeekJSONClient,
)


class DeepSeekJSONClientTests(unittest.TestCase):
    @patch("app.services.deepseek_client.httpx.post")
    def test_requests_structured_json_from_deepseek(self, post):
        response = Mock()
        response.json.return_value = {
            "choices": [
                {
                    "message": {
                        "content": '{"status": "ok"}',
                    }
                }
            ]
        }
        response.raise_for_status.return_value = None
        post.return_value = response

        client = DeepSeekJSONClient(api_key="test-key")
        result = client.request_json(
            system_prompt="Return JSON.",
            user_prompt="Test input.",
        )

        self.assertEqual(result, {"status": "ok"})
        request = post.call_args
        self.assertEqual(request.args[0], DEFAULT_DEEPSEEK_API_URL)
        self.assertEqual(request.kwargs["json"]["model"], "deepseek-v4-flash")
        self.assertEqual(
            request.kwargs["json"]["response_format"],
            {"type": "json_object"},
        )
        self.assertEqual(
            request.kwargs["json"]["thinking"],
            {"type": "disabled"},
        )
        self.assertFalse(request.kwargs["json"]["stream"])
        self.assertEqual(
            request.kwargs["json"]["max_tokens"],
            DEFAULT_MAX_TOKENS,
        )
        self.assertEqual(request.kwargs["json"]["temperature"], 0.1)
        self.assertEqual(
            request.kwargs["headers"]["Authorization"],
            "Bearer test-key",
        )

    @patch("app.services.deepseek_client.httpx.post")
    def test_retries_when_model_json_is_truncated(self, post):
        truncated_response = Mock()
        truncated_response.json.return_value = {
            "choices": [
                {
                    "finish_reason": "length",
                    "message": {"content": '{"status":'},
                }
            ]
        }
        truncated_response.raise_for_status.return_value = None
        valid_response = Mock()
        valid_response.json.return_value = {
            "choices": [
                {
                    "finish_reason": "stop",
                    "message": {"content": '{"status": "ok"}'},
                }
            ]
        }
        valid_response.raise_for_status.return_value = None
        post.side_effect = [truncated_response, valid_response]

        result = DeepSeekJSONClient(api_key="test-key").request_json(
            system_prompt="Return JSON.",
            user_prompt="Test input.",
        )

        self.assertEqual(result, {"status": "ok"})
        self.assertEqual(post.call_count, 2)
        self.assertEqual(
            post.call_args_list[1].kwargs["json"]["temperature"],
            0.2,
        )

    @patch("app.services.deepseek_client.httpx.post")
    def test_reports_token_limit_after_two_truncated_responses(self, post):
        response = Mock()
        response.json.return_value = {
            "choices": [
                {
                    "finish_reason": "length",
                    "message": {"content": '{"status":'},
                }
            ]
        }
        response.raise_for_status.return_value = None
        post.return_value = response

        with self.assertRaisesRegex(
            ValueError,
            r"16384.*截断.*2 次",
        ):
            DeepSeekJSONClient(api_key="test-key").request_json(
                system_prompt="Return JSON.",
                user_prompt="Test input.",
            )

        self.assertEqual(post.call_count, 2)

    def test_requires_deepseek_api_key(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(RuntimeError, "DEEPSEEK_API_KEY"):
                DeepSeekJSONClient()


if __name__ == "__main__":
    unittest.main()
