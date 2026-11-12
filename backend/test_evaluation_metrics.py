import unittest

from backend.evaluation_metrics import evaluate_cases


def component(role, name, quote="Evidence quote.", location="README 본문"):
    return {
        "role": role,
        "name": name,
        "evidence": [{"evidence_quote": quote, "evidence_location": location}],
    }


def fixture(components=None, declared="apache-2.0", upstream="mit", status="APPROVED"):
    return {
        "case_id": "test-case",
        "components": components or [],
        "license_analysis": {
            "declared_license": {"value": declared},
            "upstream_license": {"value": upstream},
        },
        "gold": {"expected_status": status},
    }


def prediction(components=None, declared="apache-2.0", upstream="mit", status="APPROVED"):
    return {
        "case": {
            "components": components or [],
            "license_analysis": {
                "declared_license": {"value": declared},
                "upstream_license": {"value": upstream},
            },
            "policy": {"automated_status": status},
        },
        "policy": {"status": status},
    }


class EvaluationMetricsTests(unittest.TestCase):
    def test_perfect_case_scores_all_metrics(self):
        component_gold = component("adapter", "Owner/Model")
        case = {
            "gold": fixture([component_gold]),
            "prediction": prediction([component("adapter", " owner/model ")]),
            "source_text": "Prefix Evidence   quote. Suffix",
        }

        result = evaluate_cases([case])

        self.assertEqual(result["lineage_precision"], 1.0)
        self.assertEqual(result["lineage_recall"], 1.0)
        self.assertEqual(result["lineage_f1"], 1.0)
        self.assertEqual(result["declared_license_accuracy"], 1.0)
        self.assertEqual(result["upstream_license_accuracy"], 1.0)
        self.assertEqual(result["citation_grounding_rate"], 1.0)
        self.assertEqual(result["policy_status_accuracy"], 1.0)
        self.assertEqual(result["numerators"]["lineage_precision"], 1)
        self.assertEqual(result["denominators"]["lineage_recall"], 1)
        self.assertFalse(result["cases"][0]["policy"]["unsafe_approval"])

    def test_missing_and_spurious_lineage_count_as_false_negative_and_positive(self):
        gold = fixture(
            [
                component("base_model", "org/base"),
                component("dataset", "org/data"),
            ]
        )
        predicted = prediction(
            [
                component("base_model", "org/base"),
                component("adapter", "org/extra"),
            ]
        )

        result = evaluate_cases([{"gold": gold, "prediction": predicted}])
        detail = result["cases"][0]["lineage"]

        self.assertEqual(detail["true_positive"], 1)
        self.assertEqual(detail["false_positive"], 1)
        self.assertEqual(detail["false_negative"], 1)
        self.assertEqual(result["lineage_precision"], 0.5)
        self.assertEqual(result["lineage_recall"], 0.5)
        self.assertEqual(result["lineage_f1"], 0.5)

    def test_null_gold_license_is_excluded_from_denominator(self):
        gold = fixture(declared=None, upstream="mit")
        predicted = prediction(declared="apache-2.0", upstream="MIT")

        result = evaluate_cases([{"gold": gold, "prediction": predicted}])

        self.assertEqual(result["declared_license_accuracy"], 0.0)
        self.assertEqual(result["numerators"]["declared_license_accuracy"], 0)
        self.assertEqual(result["denominators"]["declared_license_accuracy"], 0)
        self.assertEqual(result["upstream_license_accuracy"], 1.0)
        self.assertEqual(result["denominators"]["upstream_license_accuracy"], 1)

    def test_citations_include_matching_and_nonmatching_quotes(self):
        gold = fixture()
        predicted = prediction(
            [component("adapter", "owner/model", "This is cited."), component("dataset", "org/data", "Fabricated quote.")]
        )

        result = evaluate_cases(
            [{"gold": gold, "prediction": predicted, "source_text": "This is cited."}]
        )

        self.assertEqual(result["citation_grounding_rate"], 0.5)
        self.assertEqual(result["numerators"]["citation_grounding_rate"], 1)
        self.assertEqual(result["denominators"]["citation_grounding_rate"], 2)

    def test_api_metadata_evidence_is_excluded_from_readme_citation_metric(self):
        gold = fixture()
        predicted = prediction(
            [
                component("adapter", "owner/model", "This is cited."),
                component(
                    "dataset",
                    "org/data",
                    "cardData.license: apache-2.0",
                    "Hugging Face API metadata",
                ),
            ]
        )

        result = evaluate_cases(
            [{"gold": gold, "prediction": predicted, "source_text": "This is cited."}]
        )

        self.assertEqual(result["citation_grounding_rate"], 1.0)
        self.assertEqual(result["numerators"]["citation_grounding_rate"], 1)
        self.assertEqual(result["denominators"]["citation_grounding_rate"], 1)

    def test_missing_source_text_excludes_citations(self):
        result = evaluate_cases(
            [
                {
                    "gold": fixture(),
                    "prediction": prediction([component("adapter", "owner/model")]),
                }
            ]
        )

        self.assertEqual(result["citation_grounding_rate"], 0.0)
        self.assertEqual(result["denominators"]["citation_grounding_rate"], 0)

    def test_hold_or_review_approved_is_unsafe(self):
        cases = [
            {"gold": fixture(status="HOLD"), "prediction": prediction(status="APPROVED")},
            {
                "gold": fixture(status="REVIEW"),
                "prediction": prediction(status="APPROVED_WITH_ESCROW"),
            },
            {"gold": fixture(status="REVIEW"), "prediction": prediction(status="REVIEW")},
        ]

        result = evaluate_cases(cases)

        self.assertEqual(result["unsafe_approval_count"], 2)
        self.assertEqual(result["numerators"]["unsafe_approval_count"], 2)
        self.assertEqual(result["denominators"]["unsafe_approval_count"], 3)
        self.assertEqual(result["policy_status_accuracy"], 1 / 3)

    def test_empty_cases_and_zero_denominators_return_zero_rates(self):
        empty_result = evaluate_cases([])
        zero_denominator_result = evaluate_cases(
            [{"gold": fixture(), "prediction": prediction()}]
        )

        for result in (empty_result, zero_denominator_result):
            self.assertEqual(result["lineage_precision"], 0.0)
            self.assertEqual(result["lineage_recall"], 0.0)
            self.assertEqual(result["lineage_f1"], 0.0)
        self.assertEqual(empty_result["denominators"]["policy_status_accuracy"], 0)
        self.assertEqual(zero_denominator_result["denominators"]["lineage_precision"], 0)


if __name__ == "__main__":
    unittest.main()
