"""정책 엔진 테스트. 실행: python3 -m unittest discover -s backend -v

gold.expected_status 비교는 이 파일에서만 한다(정책 엔진 본문은 gold를 읽지 않음).
"""

import json
import unittest
from pathlib import Path

import policy_engine as pe

FIXTURES_DIR = Path(__file__).resolve().parent.parent / "fixtures"
FIXTURE_PATHS = sorted(FIXTURES_DIR.glob("*.json"))

HOLDER_A = "0x4D9352a9d5512BBc171107ED4CC4F67fb0261F18"
HOLDER_B = "0xcc5BDcAa07b00fc9a40E4629169E83106090672d"


def _component(role, name, license_id="apache-2.0", quote="근거 문장"):
    return {
        "role": role,
        "name": name,
        "license_id": license_id,
        "license_name": None,
        "evidence": [{"evidence_quote": quote}],
    }


def _case(components, declared="apache-2.0", upstream="apache-2.0", mismatch=False):
    return {
        "components": components,
        "license_analysis": {
            "declared_license": {"value": declared},
            "upstream_license": {"value": upstream},
            "license_metadata_mismatch": mismatch,
        },
    }


class ClassifyLicenseTest(unittest.TestCase):
    def test_categories(self):
        cases = {
            "mit": pe.ALLOWED,
            "apache-2.0": pe.ALLOWED,
            "bsd-3-clause": pe.ALLOWED,
            "cc-by-4.0": pe.ALLOWED,
            "llama3.2": pe.CONDITIONAL,
            "creativeml-openrail-m": pe.CONDITIONAL,
            "cc-by-nc-4.0": pe.PROHIBITED,
            "cc-by-nc-sa-4.0": pe.PROHIBITED,
            None: pe.UNKNOWN,
            "other": pe.UNKNOWN,
        }
        for license_id, expected in cases.items():
            with self.subTest(license_id=license_id):
                self.assertEqual(pe.classify_license(license_id), expected)

    def test_noncommercial_name(self):
        self.assertEqual(pe.classify_license("other", "Research Non-Commercial License"), pe.PROHIBITED)


class EvaluateTest(unittest.TestCase):
    def setUp(self):
        self.components = [
            _component("base_model", "org/base"),
            _component("dataset", "org/data"),
            _component("adapter", "org/adapter"),
        ]
        self.full_map = {"org/base": HOLDER_A, "org/data": HOLDER_B, "org/adapter": HOLDER_A}

    def test_all_mapped_is_approved(self):
        result = pe.evaluate(_case(self.components), self.full_map)
        self.assertEqual(result["status"], pe.APPROVED)
        self.assertEqual([r["share_bps"] for r in result["recipients"]], [4000, 3000, 3000])
        self.assertEqual(result["escrow_bps"], 0)
        self.assertEqual(result["total_bps"], 10000)

    def test_unmapped_goes_to_escrow(self):
        wallet_map = {"org/base": HOLDER_A, "org/adapter": HOLDER_A}
        result = pe.evaluate(_case(self.components), wallet_map)
        self.assertEqual(result["status"], pe.APPROVED_WITH_ESCROW)
        self.assertEqual(result["escrow_bps"], 3000)
        self.assertEqual(result["total_bps"], 10000)

    def test_prohibited_wins_over_review(self):
        self.components[2]["license_id"] = "cc-by-nc-4.0"
        result = pe.evaluate(_case(self.components, mismatch=True), self.full_map)
        self.assertEqual(result["status"], pe.HOLD)

    def test_review_conditions(self):
        variants = {
            "mismatch": (_case(self.components, mismatch=True), None),
            "mismatch_unchecked": (_case(self.components, mismatch=None), None),
            "unknown_license": (_case(self.components), ("license_id", None)),
            "empty_evidence": (_case(self.components), ("evidence", [{"evidence_quote": " "}])),
        }
        for label, (case, patch) in variants.items():
            with self.subTest(label=label):
                case = json.loads(json.dumps(case))
                if patch:
                    case["components"][1][patch[0]] = patch[1]
                self.assertEqual(pe.evaluate(case, self.full_map)["status"], pe.REVIEW)

    def test_missing_role_share_goes_to_escrow(self):
        result = pe.evaluate(_case([self.components[0], self.components[2]]), self.full_map)
        self.assertEqual(result["escrow_bps"], 3000)
        self.assertEqual(result["total_bps"], 10000)

    def test_invalid_wallet_is_unverified(self):
        wallet_map = dict(self.full_map, **{"org/data": "0x1234"})
        result = pe.evaluate(_case(self.components), wallet_map)
        self.assertEqual(result["status"], pe.APPROVED_WITH_ESCROW)

    def test_wallet_map_file_has_only_public_addresses(self):
        for name, wallet in pe.load_wallet_map().items():
            with self.subTest(name=name):
                self.assertRegex(wallet, pe.ADDRESS_PATTERN)


class FixtureGoldTest(unittest.TestCase):
    def test_fixtures_match_gold_status(self):
        self.assertTrue(FIXTURE_PATHS)
        wallet_map = pe.load_wallet_map()
        for path in FIXTURE_PATHS:
            case = json.loads(path.read_text(encoding="utf-8"))
            with self.subTest(fixture=path.name):
                result = pe.evaluate(case, wallet_map)
                self.assertEqual(result["total_bps"], 10000)
                self.assertEqual(
                    result["status"],
                    case["gold"]["expected_status"],
                    msg=f"reasons={result['reasons']}",
                )


if __name__ == "__main__":
    unittest.main()
