import copy
import json
import unittest
from pathlib import Path

from backend.extraction_validator import validate_case


FIXTURES_DIR = Path(__file__).resolve().parent.parent / "fixtures"
FIXTURE_NAMES = (
    "01_normal_paper_qa.json",
    "02_hold_noncommercial.json",
    "03_escrow_unknown_recipient.json",
    "03_review_m4xx101.json",
    "04_hash_mismatch.json",
)


def load_fixture(name: str) -> dict:
    with (FIXTURES_DIR / name).open(encoding="utf-8") as fixture_file:
        return json.load(fixture_file)


class ValidateCaseTests(unittest.TestCase):
    def test_existing_fixtures_are_valid(self) -> None:
        for fixture_name in FIXTURE_NAMES:
            with self.subTest(fixture=fixture_name):
                result = validate_case(load_fixture(fixture_name))
                self.assertTrue(result["valid"], result["errors"])
                self.assertEqual(result["errors"], [])

    def test_missing_required_values_are_reported(self) -> None:
        case = copy.deepcopy(load_fixture(FIXTURE_NAMES[0]))
        del case["input"]["model_id"]
        del case["components"][0]["role"]
        case["components"][0]["name"] = "  "
        case["components"] = case["components"][:1]
        case["components"][0]["evidence"] = "not-an-array"

        result = validate_case(case)
        error_paths = {error["path"] for error in result["errors"]}

        self.assertFalse(result["valid"])
        self.assertIn("input.model_id", error_paths)
        self.assertIn("components[0].role", error_paths)
        self.assertIn("components[0].name", error_paths)
        self.assertIn("components[0].evidence", error_paths)

    def test_empty_components_are_rejected(self) -> None:
        case = copy.deepcopy(load_fixture(FIXTURE_NAMES[0]))
        case["components"] = []

        result = validate_case(case)

        self.assertFalse(result["valid"])
        self.assertIn("components", {error["path"] for error in result["errors"]})

    def test_missing_evidence_quote_is_reported(self) -> None:
        case = copy.deepcopy(load_fixture(FIXTURE_NAMES[0]))
        del case["components"][0]["evidence"][0]["evidence_quote"]

        result = validate_case(case)

        self.assertFalse(result["valid"])
        self.assertIn(
            "components[0].evidence[0].evidence_quote",
            {error["path"] for error in result["errors"]},
        )

    def test_invalid_artifact_hash_is_reported(self) -> None:
        case = copy.deepcopy(load_fixture("03_escrow_unknown_recipient.json"))
        case["input"]["artifact_sha256"] = "not-a-sha256"

        result = validate_case(case)

        self.assertFalse(result["valid"])
        self.assertIn("input.artifact_sha256", {error["path"] for error in result["errors"]})

    def test_unknown_automated_status_is_rejected(self) -> None:
        case = copy.deepcopy(load_fixture(FIXTURE_NAMES[0]))
        for invalid_status in ("UNKNOWN", []):
            with self.subTest(status=invalid_status):
                case["policy"]["automated_status"] = invalid_status
                result = validate_case(case)

                self.assertFalse(result["valid"])
                self.assertIn(
                    "policy.automated_status",
                    {error["path"] for error in result["errors"]},
                )

    def test_missing_automated_status_is_rejected(self) -> None:
        case = copy.deepcopy(load_fixture(FIXTURE_NAMES[0]))
        del case["policy"]["automated_status"]

        result = validate_case(case)

        self.assertFalse(result["valid"])
        self.assertIn("policy.automated_status", {error["path"] for error in result["errors"]})


if __name__ == "__main__":
    unittest.main()