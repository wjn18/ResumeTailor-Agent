import os
import unittest
from unittest.mock import Mock, patch

import httpx

from app.services.model_client import LLMJSONClient


class ModelClientTests(unittest.TestCase):
    def setUp(self):
        self.environment = patch.dict(os.environ, {
            "LLM_PROVIDER": "openai_compatible",
            "LLM_API_URL": "https://models.example/v1/chat/completions",
            "LLM_MODEL": "test-model",
            "LLM_API_KEY": "test-key",
        }, clear=True)
        self.environment.start()
        self.addCleanup(self.environment.stop)

    def response(self, content='{"status":"ok"}', finish_reason="stop"):
        return Mock(json=Mock(return_value={
            "choices": [{"message": {"content": content}, "finish_reason": finish_reason}],
        }))

    @patch("app.services.model_client.httpx.post")
    def test_generic_endpoint_does_not_receive_deepseek_options(self, post):
        post.return_value = self.response()
        self.assertEqual(LLMJSONClient().request_json("JSON", "input"), {"status": "ok"})
        self.assertEqual(post.call_args.args[0], os.environ["LLM_API_URL"])
        self.assertEqual(post.call_args.kwargs["headers"]["Authorization"], "Bearer test-key")
        body = post.call_args.kwargs["json"]
        self.assertEqual(body["model"], "test-model")
        self.assertNotIn("thinking", body)
        self.assertEqual(body["response_format"], {"type": "json_object"})

    @patch("app.services.model_client.httpx.post")
    def test_capability_options_and_vendor_extension(self, post):
        os.environ.update({
            "LLM_JSON_MODE": "false",
            "LLM_TEMPERATURE": "omit",
            "LLM_TOKEN_PARAMETER": "max_completion_tokens",
            "LLM_MAX_TOKENS": "2048",
            "LLM_EXTRA_BODY": '{"reasoning_effort":"low"}',
        })
        post.return_value = self.response('```json\n{"ok":true}\n```')
        self.assertEqual(LLMJSONClient().request_json("JSON", "input"), {"ok": True})
        body = post.call_args.kwargs["json"]
        self.assertNotIn("temperature", body)
        self.assertNotIn("response_format", body)
        self.assertNotIn("max_tokens", body)
        self.assertEqual(body["max_completion_tokens"], 2048)
        self.assertEqual(body["reasoning_effort"], "low")

    @patch("app.services.model_client.httpx.post")
    def test_generic_mode_never_reuses_legacy_credentials(self, post):
        del os.environ["LLM_API_KEY"]
        os.environ["DEEPSEEK_API_KEY"] = "legacy-secret"
        post.return_value = self.response()
        LLMJSONClient().request_json("JSON", "input")
        self.assertNotIn("Authorization", post.call_args.kwargs["headers"])

    def test_generic_mode_requires_explicit_endpoint_and_model(self):
        for missing in ("LLM_API_URL", "LLM_MODEL"):
            with self.subTest(missing=missing), patch.dict(os.environ):
                del os.environ[missing]
                with self.assertRaisesRegex(RuntimeError, "LLM_MODEL and LLM_API_URL"):
                    LLMJSONClient()

    def test_all_business_clients_use_unified_configuration(self):
        from app.services.llm_client import ConfiguredResumeParser
        from app.services.jd_llm_client import ConfiguredJDParser
        from app.services.user_fact_llm_client import ConfiguredUserFactParser
        from app.services.tailoring import ConfiguredTailoringClient

        for client_class in (ConfiguredResumeParser, ConfiguredJDParser,
                             ConfiguredUserFactParser, ConfiguredTailoringClient):
            with self.subTest(client=client_class):
                client = client_class()
                self.assertIsInstance(client, LLMJSONClient)
                self.assertEqual(client.model, "test-model")
                self.assertEqual(client.provider, "openai_compatible")

    def test_legacy_environment_and_generic_precedence(self):
        with patch.dict(os.environ, {
            "DEEPSEEK_API_KEY": "legacy-key",
            "DEEPSEEK_MODEL": "legacy-model",
            "DEEPSEEK_MAX_TOKENS": "512",
        }, clear=True):
            client = LLMJSONClient()
            self.assertEqual((client.api_key, client.model, client.max_tokens),
                             ("legacy-key", "legacy-model", 512))
            self.assertEqual(client.extra_body, {"thinking": {"type": "disabled"}})
            os.environ.update({"LLM_API_KEY": "new-key", "LLM_MODEL": "new-model"})
            client = LLMJSONClient()
            self.assertEqual((client.api_key, client.model), ("new-key", "new-model"))

    def test_invalid_configuration_fails_before_network_request(self):
        invalid = {
            "LLM_PROVIDER": "unknown",
            "LLM_MAX_TOKENS": "zero",
            "LLM_JSON_MODE": "maybe",
            "LLM_TEMPERATURE": "nan",
            "LLM_TOKEN_PARAMETER": "unknown",
            "LLM_EXTRA_BODY": '{"messages":[]}',
        }
        for key, value in invalid.items():
            with self.subTest(key=key), patch.dict(os.environ, {key: value}):
                with self.assertRaises(RuntimeError):
                    LLMJSONClient()
        for value in ("[]", "null", "broken"):
            with self.subTest(extra=value), patch.dict(os.environ, {"LLM_EXTRA_BODY": value}):
                with self.assertRaisesRegex(RuntimeError, "JSON object"):
                    LLMJSONClient()

    @patch("app.services.model_client.httpx.post")
    def test_retries_bad_shape_empty_content_and_non_object_json(self, post):
        for content in ("", "[]", "null", "broken"):
            with self.subTest(content=content):
                post.reset_mock()
                post.side_effect = [self.response(content), self.response()]
                self.assertEqual(LLMJSONClient().request_json("JSON", "input"), {"status": "ok"})
                self.assertEqual(post.call_count, 2)

    @patch("app.services.model_client.httpx.post")
    def test_rejects_even_valid_json_if_marked_truncated(self, post):
        post.return_value = self.response(finish_reason="length")
        with self.assertRaisesRegex(ValueError, "截断"):
            LLMJSONClient().request_json("JSON", "input")
        self.assertEqual(post.call_count, 2)

    @patch("app.services.model_client.httpx.post")
    def test_http_error_does_not_leak_response_body_or_retry(self, post):
        response = httpx.Response(401, text="test-key", request=httpx.Request("POST", os.environ["LLM_API_URL"]))
        post.return_value = response
        with self.assertRaisesRegex(RuntimeError, "401") as raised:
            LLMJSONClient().request_json("JSON", "input")
        self.assertNotIn("test-key", str(raised.exception))
        self.assertEqual(post.call_count, 1)

    def native_environment(self, provider):
        return patch.dict(os.environ, {
            "LLM_PROVIDER": provider,
            "LLM_MODEL": "test-model",
            f"{'ANTHROPIC' if provider == 'anthropic' else 'GEMINI'}_API_KEY": "native-key",
        }, clear=True)

    @patch("app.services.model_client.httpx.post")
    def test_claude_native_protocol_and_text_blocks(self, post):
        post.return_value = Mock(json=Mock(return_value={
            "stop_reason": "end_turn",
            "content": [{"type": "thinking", "thinking": "private reasoning"},
                        {"type": "text", "text": '{"ok":'},
                        {"type": "text", "text": 'true}'}],
        }))
        with self.native_environment("anthropic"):
            result = LLMJSONClient().request_json("system", "input")
        self.assertEqual(result, {"ok": True})
        self.assertEqual(post.call_args.args[0], "https://api.anthropic.com/v1/messages")
        headers = post.call_args.kwargs["headers"]
        self.assertEqual(headers["x-api-key"], "native-key")
        self.assertEqual(headers["anthropic-version"], "2023-06-01")
        self.assertNotIn("Authorization", headers)
        body = post.call_args.kwargs["json"]
        self.assertIn("system", body)
        self.assertEqual(body["messages"], [{"role": "user", "content": "input"}])
        self.assertEqual(body["max_tokens"], 16384)
        self.assertNotIn("response_format", body)
        self.assertNotIn("thinking", body)

    @patch("app.services.model_client.httpx.post")
    def test_gemini_native_protocol_and_thought_filtering(self, post):
        post.return_value = Mock(json=Mock(return_value={
            "candidates": [{"finishReason": "STOP", "content": {"parts": [
                {"thought": True, "text": "private reasoning"},
                {"text": '{"ok":true}'},
            ]}}],
        }))
        with self.native_environment("gemini"):
            os.environ["LLM_EXTRA_BODY"] = '{"generationConfig":{"thinkingConfig":{"thinkingBudget":0}}}'
            result = LLMJSONClient().request_json("system", "input")
        self.assertEqual(result, {"ok": True})
        self.assertEqual(post.call_args.args[0], "https://generativelanguage.googleapis.com/v1beta/models/test-model:generateContent")
        headers = post.call_args.kwargs["headers"]
        self.assertEqual(headers["x-goog-api-key"], "native-key")
        self.assertNotIn("Authorization", headers)
        body = post.call_args.kwargs["json"]
        self.assertIn("systemInstruction", body)
        self.assertEqual(body["contents"], [{"role": "user", "parts": [{"text": "input"}]}])
        self.assertEqual(body["generationConfig"]["responseMimeType"], "application/json")
        self.assertEqual(body["generationConfig"]["maxOutputTokens"], 16384)
        self.assertEqual(body["generationConfig"]["thinkingConfig"], {"thinkingBudget": 0})
        self.assertNotIn("messages", body)

    @patch("app.services.model_client.httpx.post")
    def test_native_truncation_is_reported(self, post):
        for provider, payload in (
            ("anthropic", {"stop_reason": "max_tokens", "content": []}),
            ("gemini", {"candidates": [{"finishReason": "MAX_TOKENS"}]}),
        ):
            with self.subTest(provider=provider), self.native_environment(provider):
                post.reset_mock()
                post.return_value = Mock(json=Mock(return_value=payload))
                with self.assertRaisesRegex(ValueError, "截断"):
                    LLMJSONClient().request_json("system", "input")
                self.assertEqual(post.call_count, 2)

    @patch("app.services.model_client.httpx.post")
    def test_native_refusal_and_bad_shapes_fail_cleanly(self, post):
        for provider, payload in (
            ("anthropic", {"stop_reason": "refusal", "content": []}),
            ("anthropic", {"stop_reason": "end_turn", "content": None}),
            ("gemini", {"promptFeedback": {"blockReason": "SAFETY"}}),
            ("gemini", {"candidates": None}),
            ("gemini", {"candidates": [{"finishReason": "STOP", "content": {"parts": []}}]}),
        ):
            with self.subTest(provider=provider, payload=payload), self.native_environment(provider):
                post.reset_mock()
                post.return_value = Mock(json=Mock(return_value=payload))
                with self.assertRaises(ValueError):
                    LLMJSONClient().request_json("system", "input")
                self.assertEqual(post.call_count, 2)

    def test_native_credentials_required_and_configurable_options(self):
        for provider in ("anthropic", "gemini"):
            with self.subTest(provider=provider), self.native_environment(provider):
                os.environ.update({"LLM_JSON_MODE": "false", "LLM_TEMPERATURE": "omit"})
                body, _ = LLMJSONClient()._build_request("system", "input", 0)
                self.assertNotIn("temperature", body)
                self.assertNotIn("temperature", body.get("generationConfig", {}))
                self.assertNotIn("responseMimeType", body.get("generationConfig", {}))
                os.environ["LLM_API_KEY"] = ""
                with self.assertRaisesRegex(RuntimeError, "API_KEY"):
                    LLMJSONClient()

    def test_gemini_endpoint_template_and_nested_reserved_fields(self):
        with self.native_environment("gemini"):
            os.environ["LLM_MODEL"] = "models/test-model"
            os.environ["LLM_API_URL"] = "https://proxy.example/models/{model}:generateContent"
            self.assertEqual(LLMJSONClient().api_url, "https://proxy.example/models/test-model:generateContent")
            for generation in ("[]", '{"maxOutputTokens":1}', '{"responseMimeType":"text/plain"}'):
                with self.subTest(generation=generation):
                    os.environ["LLM_EXTRA_BODY"] = '{"generationConfig":' + generation + '}'
                    with self.assertRaisesRegex(RuntimeError, "generationConfig"):
                        LLMJSONClient()


if __name__ == "__main__":
    unittest.main()
