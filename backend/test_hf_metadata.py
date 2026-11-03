import json
import unittest
from io import BytesIO
from unittest.mock import patch

from backend.extraction_validator import validate_case
from backend.hf_metadata import build_case_draft, fetch_model_metadata, parse_model_id


class ParseModelIdTests(unittest.TestCase):
    def test_accepts_model_id_and_model_url(self) -> None:
        self.assertEqual(parse_model_id("owner/model"), "owner/model")
        self.assertEqual(
            parse_model_id("https://huggingface.co/owner/model"),
            "owner/model",
        )

    def test_rejects_invalid_input_with_korean_message(self) -> None:
        for value in ("", "owner", "owner/model/extra", "http://huggingface.co/owner/model"):
            with self.subTest(value=value):
                with self.assertRaisesRegex(ValueError, "모델"):
                    parse_model_id(value)


class HuggingFaceMetadataTests(unittest.TestCase):
    def setUp(self) -> None:
        self.metadata = {
            "modelId": "sample-owner/sample-model",
            "author": "sample-owner",
            "cardData": {
                "license": "apache-2.0",
                "base_model": "base-owner/base-model",
                "datasets": ["data-owner/sample-dataset"],
            },
        }

    @patch("backend.hf_metadata.urlopen")
    def test_builds_case_from_mocked_api_metadata(self, mock_urlopen) -> None:
        mock_urlopen.return_value = BytesIO(json.dumps(self.metadata).encode("utf-8"))

        case = build_case_draft("https://huggingface.co/sample-owner/sample-model", checked_at="2026-11-03")

        self.assertEqual(case["input"]["model_id"], "sample-owner/sample-model")
        self.assertEqual(case["input"]["model_url"], "https://huggingface.co/sample-owner/sample-model")
        self.assertEqual(case["input"]["checked_at"], "2026-11-03")
        self.assertEqual(case["license_analysis"]["declared_license"]["value"], "apache-2.0")
        self.assertEqual(
            {component["role"] for component in case["components"]},
            {"adapter", "base_model", "dataset"},
        )

        base_model = next(item for item in case["components"] if item["role"] == "base_model")
        dataset = next(item for item in case["components"] if item["role"] == "dataset")
        self.assertEqual(base_model["name"], "base-owner/base-model")
        self.assertEqual(dataset["name"], "data-owner/sample-dataset")
        self.assertEqual(base_model["evidence"][0]["source_url"], "https://huggingface.co/api/models/sample-owner/sample-model")
        self.assertIn("base-owner/base-model", base_model["evidence"][0]["evidence_quote"])
        self.assertEqual(case["components"][0]["publisher"], "sample-owner")
        self.assertTrue(validate_case(case)["valid"], validate_case(case)["errors"])

        request = mock_urlopen.call_args.args[0]
        self.assertEqual(request.full_url, "https://huggingface.co/api/models/sample-owner/sample-model")

    @patch("backend.hf_metadata.urlopen")
    def test_http_error_is_translated_to_korean(self, mock_urlopen) -> None:
        from urllib.error import HTTPError

        mock_urlopen.side_effect = HTTPError("https://huggingface.co/api/models/owner/model", 404, "Not Found", {}, None)

        with self.assertRaisesRegex(ValueError, "HTTP 오류"):
            fetch_model_metadata("owner/model")

    @patch("backend.hf_metadata.urlopen")
    def test_network_error_is_translated_to_korean(self, mock_urlopen) -> None:
        from urllib.error import URLError

        mock_urlopen.side_effect = URLError("offline")

        with self.assertRaisesRegex(ValueError, "연결"):
            fetch_model_metadata("owner/model")

    @patch("backend.hf_metadata.urlopen")
    def test_invalid_json_is_translated_to_korean(self, mock_urlopen) -> None:
        mock_urlopen.return_value = BytesIO(b"not-json")

        with self.assertRaisesRegex(ValueError, "JSON"):
            fetch_model_metadata("owner/model")


if __name__ == "__main__":
    unittest.main()