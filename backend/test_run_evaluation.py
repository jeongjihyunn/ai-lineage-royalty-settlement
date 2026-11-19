import json
import tempfile
import unittest
from pathlib import Path

from backend import run_evaluation as re_


README = (
    "This adapter is based on base-org/base-model.\n"
    "It was trained on data-org/sample-data.\n"
    "License: cc-by-nc-4.0."
)


def draft(model_id):
    return {
        "schema_version": "0.2",
        "case_id": "draft",
        "input": {"model_id": model_id, "model_url": f"https://huggingface.co/{model_id}", "artifact_sha256": None},
        "components": [
            {
                "role": "adapter",
                "name": model_id,
                "relationship": None,
                "publisher": None,
                "publisher_confidence": "unknown",
                "rights_holder": None,
                "rights_holder_status": "unknown",
                "license_id": "cc-by-nc-4.0",
                "license_name": None,
                "evidence": [{"evidence_quote": "cc-by-nc-4.0", "evidence_location": "API", "source_url": None}],
            }
        ],
        "license_analysis": {
            "declared_license": {"value": None, "evidence_quote": None},
            "upstream_license": {"value": None},
            "license_metadata_mismatch": None,
        },
        "policy": {"automated_status": None, "reasons": []},
    }


LLM_RESPONSE = json.dumps(
    {
        "components": [
            {"role": "base_model", "name": "base-org/base-model", "evidence_quote": "based on base-org/base-model"},
            {"role": "dataset", "name": "data-org/sample-data", "evidence_quote": "trained on data-org/sample-data"},
        ],
        "declared_license": {"value": "cc-by-nc-4.0", "evidence_quote": "License: cc-by-nc-4.0."},
    }
)


def gold_fixture(model_id, status="HOLD", split="dev", minutes=None):
    return {
        "case_id": f"gold_{model_id}",
        "input": {"model_id": model_id},
        "components": [
            {"role": "adapter", "name": model_id},
            {"role": "base_model", "name": "base-org/base-model"},
            {"role": "dataset", "name": "data-org/sample-data"},
        ],
        "license_analysis": {"declared_license": {"value": "cc-by-nc-4.0"}, "upstream_license": {"value": None}},
        "gold": {"expected_status": status, "split": split, "annotation_minutes": minutes},
    }


def fail_fetch(_model_id):
    raise ValueError("404")


class RunEvaluationTests(unittest.TestCase):
    def run_one(self, fixture, call_llm=lambda _prompt: LLM_RESPONSE, build=draft, readme=lambda _m: README):
        return re_.run_case(fixture, call_llm, build_case_draft=build, fetch_readme=readme, wallet_map={})

    def test_pipeline_runs_without_gold_and_scores(self):
        fixture = gold_fixture("org/model", minutes=12)
        run = self.run_one(fixture)
        self.assertEqual(run["outcome"], re_.OK)
        self.assertEqual(run["policy"]["status"], "HOLD")
        self.assertNotIn("gold_org/model", json.dumps(run["case"]))

        summary = re_.summarize([("a.json", fixture, run)])
        self.assertEqual(summary["metrics"]["lineage_precision"], 1.0)
        self.assertEqual(summary["metrics"]["lineage_recall"], 1.0)
        self.assertEqual(summary["metrics"]["policy_status_accuracy"], 1.0)
        self.assertEqual(summary["metrics"]["citation_grounding_rate"], 1.0)
        self.assertEqual(summary["hold_but_approved_count"], 0)
        self.assertEqual(summary["time"]["human_annotation_minutes_total"], 12)
        self.assertIn("정책 판정 일치율", re_.format_report(summary, "dev"))

    def test_fetch_failure_is_excluded_from_metrics(self):
        fixture = gold_fixture("demo/fake")
        run = self.run_one(fixture, build=fail_fetch)
        self.assertEqual(run["outcome"], re_.FETCH_FAILED)
        summary = re_.summarize([("fake.json", fixture, run)])
        self.assertEqual(summary["extraction_success"]["total"], 0)
        self.assertEqual(summary["denominators"]["policy_status_accuracy"], 0)
        self.assertEqual(summary["skipped_fetch_failed"][0]["file"], "fake.json")

    def test_extraction_failure_counts_as_wrong_but_not_unsafe(self):
        fixture = gold_fixture("org/model")
        run = self.run_one(fixture, call_llm=lambda _p: "not json")
        self.assertEqual(run["outcome"], re_.EXTRACTION_FAILED)
        summary = re_.summarize([("a.json", fixture, run)])
        self.assertEqual(summary["denominators"]["policy_status_accuracy"], 1)
        self.assertEqual(summary["metrics"]["policy_status_accuracy"], 0.0)
        self.assertEqual(summary["metrics"]["unsafe_approval_count"], 0)

    def test_load_fixtures_filters_by_split(self):
        with tempfile.TemporaryDirectory() as tmp:
            for name, split in (("a.json", "dev"), ("b.json", "eval")):
                Path(tmp, name).write_text(json.dumps(gold_fixture(name, split=split)), encoding="utf-8")
            self.assertEqual([n for n, _ in re_.load_fixtures("eval", Path(tmp))], ["b.json"])

    def test_eval_split_requires_final_flag(self):
        self.assertEqual(re_.main(["--split", "eval"]), 2)


if __name__ == "__main__":
    unittest.main()
