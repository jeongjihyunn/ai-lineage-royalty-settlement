import json
import unittest

from backend.llm_extractor import build_extraction_prompt, extract_case_from_readme


class LLMExtractorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.readme = (
            "This adapter is based on base-org/base-model.\n"
            "It was trained on data-org/sample-data.\n"
            "License: Apache-2.0."
        )
        self.case_draft = {
            "schema_version": "0.2",
            "case_id": "hf_demo_model_20261103",
            "input": {
                "model_id": "demo/model",
                "model_url": "https://huggingface.co/demo/model",
                "checked_at": "2026-11-03",
            },
            "components": [
                {
                    "role": "adapter",
                    "name": "demo/model",
                    "relationship": None,
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
        self.llm_response = {
            "components": [
                {
                    "role": "base_model",
                    "name": "base-org/base-model",
                    "evidence_quote": "based on base-org/base-model",
                },
                {
                    "role": "dataset",
                    "name": "data-org/sample-data",
                    "evidence_quote": "trained on data-org/sample-data",
                },
            ],
            "declared_license": {
                "value": "Apache-2.0",
                "evidence_quote": "License: Apache-2.0.",
            },
        }

    def test_builds_korean_prompt_with_json_only_requirements(self) -> None:
        prompt = build_extraction_prompt(self.readme, self.case_draft)

        self.assertIn("법적 판단", prompt)
        self.assertIn("JSON", prompt)
        self.assertIn(self.readme, prompt)

    def test_valid_json_generates_case_and_verifies_quotes(self) -> None:
        result = extract_case_from_readme(
            self.readme,
            self.case_draft,
            lambda prompt: json.dumps(self.llm_response, ensure_ascii=False),
        )

        self.assertTrue(result["valid"], result["errors"])
        self.assertEqual(result["errors"], [])
        case = result["case"]
        self.assertEqual(case["license_analysis"]["declared_license"]["value"], "Apache-2.0")
        self.assertIsNone(case["license_analysis"]["license_metadata_mismatch"])
        self.assertIsNone(case["policy"]["automated_status"])
        roles = {component["role"] for component in case["components"]}
        self.assertTrue({"adapter", "base_model", "dataset"}.issubset(roles))
        base_model = next(item for item in case["components"] if item["role"] == "base_model")
        self.assertEqual(base_model["evidence"][0]["source_url"], "https://huggingface.co/demo/model/raw/main/README.md")

    def test_quote_whitespace_is_normalized_for_matching(self) -> None:
        self.llm_response["components"][0]["evidence_quote"] = "based on\n base-org/base-model"

        result = extract_case_from_readme(
            self.readme,
            self.case_draft,
            lambda prompt: json.dumps(self.llm_response),
        )

        self.assertTrue(result["valid"], result["errors"])

    def test_quote_missing_from_readme_fails_closed(self) -> None:
        self.llm_response["components"][0]["evidence_quote"] = "The model was created by someone else."

        result = extract_case_from_readme(
            self.readme,
            self.case_draft,
            lambda prompt: json.dumps(self.llm_response),
        )

        self.assertFalse(result["valid"])
        self.assertIsNone(result["case"])
        self.assertIn("README 본문에 없습니다", result["errors"][0]["message"])

    def test_empty_quote_fails(self) -> None:
        self.llm_response["components"][0]["evidence_quote"] = "  "

        result = extract_case_from_readme(
            self.readme,
            self.case_draft,
            lambda prompt: json.dumps(self.llm_response),
        )

        self.assertFalse(result["valid"])
        self.assertIsNone(result["case"])

    def test_non_json_llm_response_fails_closed(self) -> None:
        result = extract_case_from_readme(self.readme, self.case_draft, lambda prompt: "not JSON")

        self.assertFalse(result["valid"])
        self.assertIsNone(result["case"])
        self.assertIn("JSON이 아닙니다", result["errors"][0]["message"])

    def test_malformed_role_value_fails_closed(self) -> None:
        self.llm_response["components"][0]["role"] = []

        result = extract_case_from_readme(
            self.readme,
            self.case_draft,
            lambda prompt: json.dumps(self.llm_response),
        )

        self.assertFalse(result["valid"])
        self.assertIsNone(result["case"])
        self.assertEqual(result["errors"][0]["path"], "components[0].role")


if __name__ == "__main__":
    unittest.main()