"""정책 엔진 테스트. 실행: python3 -m unittest discover -s backend -v

gold.expected_status 비교는 이 파일에서만 한다(정책 엔진 본문은 gold를 읽지 않음).
"""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import policy_engine as pe

FIXTURES_DIR = Path(__file__).resolve().parent.parent / "fixtures"
FIXTURE_PATHS = sorted(FIXTURES_DIR.glob("*.json"))

HOLDER_A = "0x4D9352a9d5512BBc171107ED4CC4F67fb0261F18"
HOLDER_B = "0xcc5BDcAa07b00fc9a40E4629169E83106090672d"
HOLDER_C = "0x69F926Dd50FD6D48F2d89dCEB5d425FE861f99e3"


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


class NormalizeLicenseTest(unittest.TestCase):
    def test_same_license_different_spelling(self):
        groups = {
            "apache-2.0": ["Apache License 2.0", "apache 2.0", "apache-2.0", "Apache License, Version 2.0", "APACHE_2.0"],
            "mit": ["MIT", "MIT License", "mit"],
            "cc-by-4.0": ["CC BY 4.0", "cc-by-4.0", "Creative Commons Attribution 4.0"],
            "cc-by-nc-4.0": ["CC BY-NC 4.0", "Creative Commons Attribution-NonCommercial 4.0"],
            "llama3.2": ["llama3.2", "Llama 3.2 Community License"],
            "bsd-3-clause": ["BSD 3-Clause License", "bsd-3-clause"],
        }
        for expected, spellings in groups.items():
            for spelling in spellings:
                with self.subTest(spelling=spelling):
                    self.assertEqual(pe.normalize_license(spelling), expected)

    def test_empty_values(self):
        for value in (None, "", "   ", 3, "License"):
            with self.subTest(value=value):
                self.assertIsNone(pe.normalize_license(value))

    def test_classify_uses_normalized_id_or_name(self):
        self.assertEqual(pe.classify_license("Apache License 2.0"), pe.ALLOWED)
        self.assertEqual(pe.classify_license(None, "Llama 3.2 Community License"), pe.CONDITIONAL)
        self.assertEqual(pe.classify_license("CC BY-NC 4.0"), pe.PROHIBITED)


class PolicyConfigTest(unittest.TestCase):
    def test_config_file_drives_weights(self):
        self.assertEqual(pe.ROLE_WEIGHTS_BPS, {"base_model": 4000, "dataset": 3000, "adapter": 3000})
        self.assertEqual(sum(pe.ROLE_WEIGHTS_BPS.values()), pe.TOTAL_BPS)

    def test_custom_config(self):
        config = json.loads(json.dumps(pe.CONFIG))
        config["role_weights_bps"] = {"base_model": 5000, "dataset": 3000, "adapter": 2000}
        config["license_classes"]["allowed"]["ids"].append("custom-ok")
        components = [
            _component("base_model", "org/base", "custom-ok"),
            _component("dataset", "org/data"),
            _component("adapter", "org/adapter"),
        ]
        wallet_map = {"org/base": HOLDER_A, "org/data": HOLDER_B, "org/adapter": HOLDER_C}
        result = pe.evaluate(_case(components), wallet_map, config)
        self.assertEqual(result["status"], pe.APPROVED)
        self.assertEqual([r["share_bps"] for r in result["recipients"]], [5000, 3000, 2000])

    def test_bad_weight_sum_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.json"
            config = json.loads(json.dumps(pe.CONFIG))
            config["role_weights_bps"]["adapter"] = 1000
            path.write_text(json.dumps(config), encoding="utf-8")
            with self.assertRaises(ValueError):
                pe.load_policy_config(path)


class ReasonEvidenceTest(unittest.TestCase):
    def test_reasons_carry_evidence(self):
        wallet_map = pe.load_wallet_map()
        for path in FIXTURE_PATHS:
            case = json.loads(path.read_text(encoding="utf-8"))
            with self.subTest(fixture=path.name):
                result = pe.evaluate(case, wallet_map)
                self.assertTrue(result["reasons"])
                for reason in result["reasons"]:
                    self.assertEqual(set(reason), {"message", "target", "evidence"})
                    for ev in reason["evidence"]:
                        self.assertEqual(set(ev), {"evidence_quote", "evidence_location", "source_url"})
                        self.assertTrue(ev["evidence_quote"].strip())

    def test_hold_reason_quotes_source(self):
        case = json.loads((FIXTURES_DIR / "02_hold_noncommercial.json").read_text(encoding="utf-8"))
        result = pe.evaluate(case, pe.load_wallet_map())
        quotes = [ev["evidence_quote"] for r in result["reasons"] for ev in r["evidence"]]
        self.assertIn(
            "The LoRA adapter weights and accompanying files are licensed under CC-BY-NC-4.0.", quotes
        )

    def test_engine_ignores_gold_and_assessment(self):
        case = json.loads((FIXTURES_DIR / "01_normal_paper_qa.json").read_text(encoding="utf-8"))
        baseline = pe.evaluate(case, pe.load_wallet_map())
        case["gold"] = {"expected_status": "HOLD"}
        case["license_analysis"]["commercial_use_assessment"] = "not_allowed"
        self.assertEqual(pe.evaluate(case, pe.load_wallet_map()), baseline)


class ToContractArgsTest(unittest.TestCase):
    MODEL_HASH = "4ef4de62188b4b3718258d63a1a6d9ad90416aa2c578abd67fabe69a7111a352"
    EVIDENCE_HASH = "0x" + "ab" * 32

    def _result(self, recipients, escrow_bps=0, status=pe.APPROVED):
        return {"status": status, "recipients": recipients, "escrow_bps": escrow_bps}

    def test_fixture_args(self):
        case = json.loads((FIXTURES_DIR / "03_escrow_unknown_recipient.json").read_text(encoding="utf-8"))
        result = pe.evaluate(case, pe.load_wallet_map())
        args = pe.to_contract_args(result, self.MODEL_HASH, self.EVIDENCE_HASH)
        self.assertEqual(list(args), ["modelHash", "recipients", "sharesBps", "escrowBps", "evidenceHash"])
        self.assertEqual(args["modelHash"], "0x" + self.MODEL_HASH)
        self.assertEqual(args["recipients"], [HOLDER_A, HOLDER_C])
        self.assertEqual(args["sharesBps"], [4000, 3000])
        self.assertEqual(args["escrowBps"], 3000)

    def test_rejects_invalid_inputs(self):
        ok = [{"wallet": HOLDER_A, "share_bps": 6000}, {"wallet": HOLDER_B, "share_bps": 4000}]
        invalid = {
            "hold_status": (self._result(ok, status=pe.HOLD), self.MODEL_HASH),
            "review_status": (self._result(ok, status=pe.REVIEW), self.MODEL_HASH),
            "bad_address": (self._result([{"wallet": "0x1234", "share_bps": 10000}]), self.MODEL_HASH),
            "zero_address": (self._result([{"wallet": pe.ZERO_ADDRESS, "share_bps": 10000}]), self.MODEL_HASH),
            "duplicate": (
                self._result([{"wallet": HOLDER_A, "share_bps": 5000}, {"wallet": HOLDER_A.lower(), "share_bps": 5000}]),
                self.MODEL_HASH,
            ),
            "sum_not_10000": (self._result(ok, escrow_bps=1), self.MODEL_HASH),
            "zero_share": (self._result([{"wallet": HOLDER_A, "share_bps": 0}], escrow_bps=10000), self.MODEL_HASH),
            "no_recipients": (self._result([], escrow_bps=10000), self.MODEL_HASH),
            "bad_model_hash": (self._result(ok), "0x1234"),
            "zero_model_hash": (self._result(ok), "0x" + "0" * 64),
        }
        for label, (result, model_hash) in invalid.items():
            with self.subTest(label=label):
                with self.assertRaises(ValueError):
                    pe.to_contract_args(result, model_hash, self.EVIDENCE_HASH)

    def test_rejects_bad_evidence_hash(self):
        result = self._result([{"wallet": HOLDER_A, "share_bps": 10000}])
        with self.assertRaises(ValueError):
            pe.to_contract_args(result, self.MODEL_HASH, None)


class EdgeCaseTest(unittest.TestCase):
    """입력이 비거나 깨져도 엔진이 죽지 않고 REVIEW를 내는지 확인한다."""

    def assert_safe_review(self, case):
        result = pe.evaluate(case, {"org/base": HOLDER_A})
        self.assertEqual(result["status"], pe.REVIEW)
        self.assertEqual(result["total_bps"], pe.TOTAL_BPS)
        self.assertTrue(result["reasons"])
        return result

    def test_empty_or_null_case(self):
        for case in ({}, None, {"components": None, "license_analysis": None}):
            with self.subTest(case=case):
                self.assert_safe_review(case)

    def test_empty_components(self):
        result = self.assert_safe_review(_case([]))
        self.assertEqual(result["recipients"], [])
        self.assertEqual(result["escrow_bps"], pe.TOTAL_BPS)

    def test_components_not_a_list(self):
        self.assert_safe_review(_case({"role": "base_model"}))

    def test_component_with_null_fields(self):
        component = {key: None for key in ("role", "name", "license_id", "license_name", "evidence")}
        self.assert_safe_review(_case([component]))

    def test_component_not_a_dict(self):
        self.assert_safe_review(_case([None, "base_model"]))

    def test_evidence_with_null_entries(self):
        component = _component("base_model", "org/base")
        component["evidence"] = [None, {"evidence_quote": None}, {}]
        self.assert_safe_review(_case([component]))

    def test_unknown_role(self):
        components = [_component("base_model", "org/base"), _component("merged_from", "org/merged")]
        result = self.assert_safe_review(_case(components))
        self.assertTrue(any("merged_from" in r["message"] for r in result["reasons"]))

    def test_null_license_analysis_fields(self):
        case = _case([_component("base_model", "org/base")])
        case["license_analysis"] = {"declared_license": None, "upstream_license": {}, "license_metadata_mismatch": None}
        self.assert_safe_review(case)


class CliTest(unittest.TestCase):
    ENGINE = Path(pe.__file__).resolve()

    def run_cli(self, *args):
        return subprocess.run(
            [sys.executable, str(self.ENGINE), *args], capture_output=True, text=True, timeout=30
        )

    def test_prints_status_reasons_and_shares(self):
        proc = self.run_cli(str(FIXTURES_DIR / "03_escrow_unknown_recipient.json"))
        self.assertEqual(proc.returncode, 0, proc.stderr)
        for text in ("상태: APPROVED_WITH_ESCROW", "근거:", HOLDER_A, "4000 bps", "escrow", "10000 bps"):
            self.assertIn(text, proc.stdout)

    def test_missing_file(self):
        proc = self.run_cli("does-not-exist.json")
        self.assertEqual(proc.returncode, 2)


if __name__ == "__main__":
    unittest.main()
