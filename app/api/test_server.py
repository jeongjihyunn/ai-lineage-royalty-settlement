import json
import os
import sys
import types
import unittest
from unittest import mock

from app.api import server
from fastapi.testclient import TestClient


def case(name):
    return json.loads((server.FIXTURES_DIR / f"{name}.json").read_text())


class DepositTest(unittest.TestCase):
    def test_not_approved_is_refused_before_network(self):
        for name in ("02_hold_noncommercial", "03_review_m4xx101"):
            with mock.patch.object(server.wa, "deposit") as sent:
                result = server.deposit(case(name), "0.001")
            sent.assert_not_called()
            self.assertEqual(result["status"], server.wa.REJECTED)
            self.assertEqual(result["error_code"], "NOT_APPROVED")

    def test_approved_sends_wei(self):
        with mock.patch.object(server.wa, "deposit", return_value={"ok": True}) as sent:
            server.deposit(case("03_escrow_unknown_recipient"), "0.001")
        self.assertEqual(sent.call_args.args[1], 10**15)
        self.assertTrue(sent.call_args.kwargs["send"])

    def test_bad_amount_is_not_sent(self):
        for amount in ("abc", "NaN", "Infinity", "0.0000000000000000001"):
            with mock.patch.object(server.wa, "deposit") as sent:
                result = server.deposit(case("03_escrow_unknown_recipient"), amount)
            sent.assert_not_called()
            self.assertEqual(result["error_code"], "INVALID_AMOUNT")

    def test_tampered_example_deposits_with_tampered_hash(self):
        with mock.patch.object(server.wa, "deposit", return_value={"ok": False}) as sent:
            server.deposit_example("04_hash_mismatch", "0.001")
        self.assertEqual(sent.call_args.args[0], server.EXAMPLE_TAMPERED_HASHES["04_hash_mismatch"])

    def test_tampered_example_cannot_register(self):
        self.assertFalse(server.get_example("04_hash_mismatch")["register"]["ok"])
        with mock.patch.object(server.wr, "register_settlement_rule") as sent:
            with self.assertRaises(server.HTTPException):
                server.register_example("04_hash_mismatch")
        sent.assert_not_called()

    def test_release_moves_whole_escrow_to_holder(self):
        current = {"registered": True, "escrow_eth": "0.00003"}
        with mock.patch.object(server, "settlement", return_value=current), \
                mock.patch.object(server.wa, "release_escrow", return_value={"ok": True}) as sent:
            server.release_example("03_escrow_unknown_recipient")
        args = sent.call_args.args
        self.assertEqual(args[1], server.wa.load_holder_addresses()["Holder_B"])
        self.assertEqual(args[2], 3 * 10**13)
        self.assertEqual(len(args[3]), 64)
        self.assertTrue(sent.call_args.kwargs["send"])

    def test_release_only_for_escrow_example(self):
        with self.assertRaises(server.HTTPException):
            server.release_example("01_normal_paper_qa")

    def test_eth_format(self):
        self.assertEqual(server.eth(0), "0")
        self.assertEqual(server.eth(10 * 10**18), "10")
        self.assertEqual(server.eth(10**15), "0.001")


class ExtractApiTest(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(server.app)
        self.metadata = {
            "modelId": "demo/model",
            "author": "demo",
            "cardData": {"license": "apache-2.0", "base_model": "base-org/base-model"},
        }
        self.readme = "This adapter is based on base-org/base-model.\nLicense: Apache-2.0."
        self.llm_response = {
            "components": [
                {
                    "role": "base_model",
                    "name": "base-org/base-model",
                    "evidence_quote": "This adapter is based on base-org/base-model.",
                }
            ],
            "declared_license": {
                "value": "Apache-2.0",
                "evidence_quote": "License: Apache-2.0.",
            },
        }

    def post_with_mocked_hf(self, readme=None):
        readme = self.readme if readme is None else readme
        return (
            mock.patch.object(server.hm, "fetch_model_metadata", return_value=self.metadata),
            mock.patch.object(server.hr, "fetch_readme", return_value=readme),
        )

    def mock_openai(self, response=None, error=None):
        client = mock.Mock()
        if error is not None:
            client.chat.completions.create.side_effect = error
        else:
            client.chat.completions.create.return_value.choices = [
                types.SimpleNamespace(
                    message=types.SimpleNamespace(
                        content=json.dumps(response or self.llm_response, ensure_ascii=False)
                    )
                )
            ]
        sdk = types.ModuleType("openai")
        sdk.OpenAI = mock.Mock(return_value=client)
        return sdk, client

    def mock_anthropic(self):
        client = mock.Mock()
        client.messages.create.return_value.content = [
            types.SimpleNamespace(
                type="text",
                text=json.dumps(self.llm_response, ensure_ascii=False),
            )
        ]
        sdk = types.ModuleType("anthropic")
        sdk.Anthropic = mock.Mock(return_value=client)
        return sdk, client

    def test_extract_success_uses_mocked_provider_and_returns_policy(self):
        sdk, client = self.mock_openai()
        metadata_call, readme_call = self.post_with_mocked_hf()
        settings = {
            "LLM_PROVIDER": "openai",
            "LLM_MODEL": "test-model",
            "OPENAI_API_KEY": "test-key-not-real",
        }
        with metadata_call as metadata_mock, readme_call as readme_mock, \
                mock.patch.dict(os.environ, settings, clear=True), \
                mock.patch.dict(sys.modules, {"openai": sdk}), \
                mock.patch.object(server.wr, "register_settlement_rule") as register, \
                mock.patch.object(server.wa, "deposit") as deposit, \
                mock.patch.object(server.wa, "claim") as claim, \
                mock.patch.object(server.pe, "to_contract_args") as contract_args:
            response = self.client.post("/api/extract", json={"model": "demo/model"})

        self.assertEqual(response.status_code, 200, response.text)
        result = response.json()
        self.assertTrue(result["valid"])
        self.assertTrue(result["validation"]["valid"])
        self.assertIn(result["policy"]["status"], {"APPROVED", "APPROVED_WITH_ESCROW", "HOLD", "REVIEW"})
        self.assertEqual(result["case"]["policy"]["automated_status"], result["policy"]["status"])
        metadata_mock.assert_called_once_with("demo/model")
        readme_mock.assert_called_once_with("demo/model")
        client.chat.completions.create.assert_called_once()
        register.assert_not_called()
        deposit.assert_not_called()
        claim.assert_not_called()
        contract_args.assert_not_called()

    def test_extract_success_with_anthropic_provider(self):
        sdk, client = self.mock_anthropic()
        metadata_call, readme_call = self.post_with_mocked_hf()
        settings = {
            "LLM_PROVIDER": "anthropic",
            "LLM_MODEL": "test-model",
            "ANTHROPIC_API_KEY": "test-key-not-real",
        }
        with metadata_call, readme_call, \
                mock.patch.dict(os.environ, settings, clear=True), \
                mock.patch.dict(sys.modules, {"anthropic": sdk}):
            response = self.client.post("/api/extract", json={"model": "demo/model"})

        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(response.json()["valid"])
        client.messages.create.assert_called_once()

    def test_invalid_model_id_returns_400_before_hugging_face_lookup(self):
        with mock.patch.object(server.hm, "fetch_model_metadata") as metadata:
            response = self.client.post("/api/extract", json={"model": "https://example.com/owner/model"})

        self.assertEqual(response.status_code, 400)
        self.assertFalse(response.json()["valid"])
        self.assertIsNone(response.json()["policy"])
        metadata.assert_not_called()

    def test_readme_failure_returns_safe_korean_error(self):
        metadata_call, _ = self.post_with_mocked_hf()
        with metadata_call, \
                mock.patch.object(server.hr, "fetch_readme", side_effect=RuntimeError("private source detail")), \
                mock.patch.object(server, "build_call_llm_from_env") as provider:
            response = self.client.post("/api/extract", json={"model": "demo/model"})

        self.assertEqual(response.status_code, 502)
        self.assertIn("README", response.json()["errors"][0]["message"])
        self.assertNotIn("private source detail", response.text)
        provider.assert_not_called()

    def test_missing_provider_configuration_returns_safe_error(self):
        metadata_call, readme_call = self.post_with_mocked_hf()
        with metadata_call, readme_call, mock.patch.dict(os.environ, {}, clear=True):
            response = self.client.post("/api/extract", json={"model": "demo/model"})

        self.assertEqual(response.status_code, 503)
        self.assertIn("LLM_PROVIDER", response.json()["errors"][0]["message"])
        self.assertNotIn("test-key", response.text)

    def test_llm_call_failure_returns_safe_error_without_policy(self):
        sdk, _ = self.mock_openai(error=RuntimeError("provider failed with test-key-not-real"))
        metadata_call, readme_call = self.post_with_mocked_hf()
        settings = {
            "LLM_PROVIDER": "openai",
            "LLM_MODEL": "test-model",
            "OPENAI_API_KEY": "test-key-not-real",
        }
        with metadata_call, readme_call, \
                mock.patch.dict(os.environ, settings, clear=True), \
                mock.patch.dict(sys.modules, {"openai": sdk}), \
                mock.patch.object(server.pe, "evaluate") as policy:
            response = self.client.post("/api/extract", json={"model": "demo/model"})

        self.assertEqual(response.status_code, 422)
        self.assertIn("LLM 호출 함수 실행에 실패", response.json()["errors"][0]["message"])
        self.assertNotIn("test-key-not-real", response.text)
        policy.assert_not_called()

    def test_invalid_quote_returns_errors_without_policy_or_registration(self):
        invalid_response = {
            "components": [
                {
                    "role": "base_model",
                    "name": "base-org/base-model",
                    "evidence_quote": "This sentence is not in the README.",
                }
            ],
            "declared_license": {"value": None, "evidence_quote": None},
        }
        sdk, _ = self.mock_openai(response=invalid_response)
        metadata_call, readme_call = self.post_with_mocked_hf()
        settings = {
            "LLM_PROVIDER": "openai",
            "LLM_MODEL": "test-model",
            "OPENAI_API_KEY": "test-key-not-real",
        }
        with metadata_call, readme_call, \
                mock.patch.dict(os.environ, settings, clear=True), \
                mock.patch.dict(sys.modules, {"openai": sdk}), \
                mock.patch.object(server.pe, "evaluate") as policy, \
                mock.patch.object(server.wr, "register_settlement_rule") as register, \
                mock.patch.object(server.pe, "to_contract_args") as contract_args:
            response = self.client.post("/api/extract", json={"model": "demo/model"})

        self.assertEqual(response.status_code, 422)
        result = response.json()
        self.assertFalse(result["valid"])
        self.assertIsNone(result["case"])
        self.assertIsNone(result["policy"])
        self.assertTrue(result["errors"])
        policy.assert_not_called()
        register.assert_not_called()
        contract_args.assert_not_called()


if __name__ == "__main__":
    unittest.main()
