import json
import os
import sys
import types
import unittest
from unittest import mock

from backend.llm_extractor import extract_case_from_readme
from backend.llm_provider import build_call_llm_from_env


class LLMProviderTests(unittest.TestCase):
    def settings(self, provider: str = "openai", **overrides: str) -> dict[str, str]:
        values = {
            "LLM_PROVIDER": provider,
            "LLM_MODEL": "test-model",
            "OPENAI_API_KEY": "openai-test-key",
            "ANTHROPIC_API_KEY": "anthropic-test-key",
        }
        values.update(overrides)
        return values

    def openai_sdk(self, client: mock.Mock) -> types.ModuleType:
        sdk = types.ModuleType("openai")
        sdk.OpenAI = mock.Mock(return_value=client)
        return sdk

    def anthropic_sdk(self, client: mock.Mock) -> types.ModuleType:
        sdk = types.ModuleType("anthropic")
        sdk.Anthropic = mock.Mock(return_value=client)
        return sdk

    def test_openai_provider_returns_response_text(self) -> None:
        client = mock.Mock()
        client.chat.completions.create.return_value.choices = [
            types.SimpleNamespace(message=types.SimpleNamespace(content='{"ok": true}'))
        ]
        sdk = self.openai_sdk(client)

        with mock.patch.dict(os.environ, self.settings(), clear=True), \
                mock.patch.dict(sys.modules, {"openai": sdk}):
            call_llm = build_call_llm_from_env()
            result = call_llm("테스트 프롬프트")

        self.assertEqual(result, '{"ok": true}')
        client.chat.completions.create.assert_called_once_with(
            model="test-model",
            messages=[{"role": "user", "content": "테스트 프롬프트"}],
            temperature=0,
        )

    def test_anthropic_provider_returns_text_blocks(self) -> None:
        client = mock.Mock()
        client.messages.create.return_value.content = [
            types.SimpleNamespace(type="text", text="응답 "),
            types.SimpleNamespace(type="tool_use", text=None),
            types.SimpleNamespace(type="text", text="문장"),
        ]
        sdk = self.anthropic_sdk(client)

        with mock.patch.dict(os.environ, self.settings("anthropic"), clear=True), \
                mock.patch.dict(sys.modules, {"anthropic": sdk}):
            call_llm = build_call_llm_from_env()
            result = call_llm("테스트 프롬프트")

        self.assertEqual(result, "응답 문장")
        client.messages.create.assert_called_once_with(
            model="test-model",
            max_tokens=4096,
            messages=[{"role": "user", "content": "테스트 프롬프트"}],
            temperature=0,
        )

    def test_missing_provider_raises_korean_value_error(self) -> None:
        with mock.patch.dict(os.environ, {}, clear=True), self.assertRaises(ValueError) as raised:
            build_call_llm_from_env()

        self.assertIn("LLM_PROVIDER", str(raised.exception))

    def test_missing_model_raises_korean_value_error(self) -> None:
        settings = self.settings()
        del settings["LLM_MODEL"]
        with mock.patch.dict(os.environ, settings, clear=True), self.assertRaises(ValueError) as raised:
            build_call_llm_from_env()

        self.assertIn("LLM_MODEL", str(raised.exception))

    def test_missing_provider_key_raises_korean_value_error(self) -> None:
        for provider, key_name in (("openai", "OPENAI_API_KEY"), ("anthropic", "ANTHROPIC_API_KEY")):
            with self.subTest(provider=provider):
                settings = self.settings(provider)
                del settings[key_name]
                with mock.patch.dict(os.environ, settings, clear=True), self.assertRaises(ValueError) as raised:
                    build_call_llm_from_env()

                self.assertIn(key_name, str(raised.exception))
                self.assertNotIn("test-key", str(raised.exception))

    def test_unsupported_provider_raises_korean_value_error(self) -> None:
        with mock.patch.dict(os.environ, self.settings("other"), clear=True), \
                self.assertRaises(ValueError) as raised:
            build_call_llm_from_env()

        self.assertIn("지원하지 않는", str(raised.exception))

    def test_openai_call_failure_does_not_expose_api_key(self) -> None:
        client = mock.Mock()
        client.chat.completions.create.side_effect = RuntimeError("failed with openai-test-key")
        sdk = self.openai_sdk(client)

        with mock.patch.dict(os.environ, self.settings(), clear=True), \
                mock.patch.dict(sys.modules, {"openai": sdk}):
            call_llm = build_call_llm_from_env()
            with self.assertRaises(RuntimeError) as raised:
                call_llm("테스트 프롬프트")

        self.assertIn("OpenAI API 호출에 실패", str(raised.exception))
        self.assertNotIn("openai-test-key", str(raised.exception))
        self.assertIsNone(raised.exception.__cause__)

    def test_anthropic_call_failure_does_not_expose_api_key(self) -> None:
        client = mock.Mock()
        client.messages.create.side_effect = RuntimeError("failed with anthropic-test-key")
        sdk = self.anthropic_sdk(client)

        with mock.patch.dict(os.environ, self.settings("anthropic"), clear=True), \
                mock.patch.dict(sys.modules, {"anthropic": sdk}):
            call_llm = build_call_llm_from_env()
            with self.assertRaises(RuntimeError) as raised:
                call_llm("테스트 프롬프트")

        self.assertIn("Anthropic API 호출에 실패", str(raised.exception))
        self.assertNotIn("anthropic-test-key", str(raised.exception))
        self.assertIsNone(raised.exception.__cause__)

    def test_provider_callable_is_accepted_by_extractor(self) -> None:
        llm_response = {
            "components": [
                {
                    "role": "base_model",
                    "name": "base-org/base-model",
                    "evidence_quote": "based on base-org/base-model",
                }
            ],
            "declared_license": {"value": None, "evidence_quote": None},
        }
        client = mock.Mock()
        client.chat.completions.create.return_value.choices = [
            types.SimpleNamespace(
                message=types.SimpleNamespace(content=json.dumps(llm_response))
            )
        ]
        sdk = self.openai_sdk(client)
        readme = "This adapter is based on base-org/base-model."
        case_draft = {
            "input": {"model_id": "demo/model", "model_url": "https://huggingface.co/demo/model"},
            "components": [
                {
                    "role": "adapter",
                    "name": "demo/model",
                    "relationship": "adapter_of",
                    "publisher": "demo",
                    "publisher_confidence": "unknown",
                    "rights_holder": None,
                    "rights_holder_status": "unknown",
                    "license_id": None,
                    "license_name": None,
                    "evidence": [],
                }
            ],
            "license_analysis": {
                "declared_license": {"value": None, "evidence_quote": None},
                "upstream_license": {"value": None},
                "license_metadata_mismatch": False,
            },
            "policy": {"automated_status": "APPROVED", "reasons": []},
        }

        with mock.patch.dict(os.environ, self.settings(), clear=True), \
                mock.patch.dict(sys.modules, {"openai": sdk}):
            call_llm = build_call_llm_from_env()
            result = extract_case_from_readme(readme, case_draft, call_llm=call_llm)

        self.assertTrue(result["valid"], result["errors"])
        self.assertIn("base_model", {component["role"] for component in result["case"]["components"]})


if __name__ == "__main__":
    unittest.main()
