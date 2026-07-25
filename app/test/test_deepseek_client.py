import os
import unittest
from unittest.mock import Mock, patch

from app.services.deepseek_client import (
    DEFAULT_DEEPSEEK_API_URL,
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
            request.kwargs["headers"]["Authorization"],
            "Bearer test-key",
        )

    def test_requires_deepseek_api_key(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(RuntimeError, "DEEPSEEK_API_KEY"):
                DeepSeekJSONClient()


if __name__ == "__main__":
    unittest.main()
