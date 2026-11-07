"""Gold fixture과 추출·정책 결과를 비교하는 오프라인 평가 지표."""

from __future__ import annotations

from collections import Counter
import re
from typing import Any


_WHITESPACE = re.compile(r"\s+")
_APPROVED_STATUSES = {"APPROVED", "APPROVED_WITH_ESCROW"}
_UNSAFE_GOLD_STATUSES = {"HOLD", "REVIEW"}


def _mapping(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _sequence(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _normalize_text(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = _WHITESPACE.sub(" ", value).strip().casefold()
    return normalized or None


def _lineage_pairs(case: Any) -> Counter[tuple[str, str]]:
    components = _sequence(_mapping(case).get("components"))
    pairs: Counter[tuple[str, str]] = Counter()
    for component in components:
        component = _mapping(component)
        role = component.get("role")
        name = _normalize_text(component.get("name"))
        if isinstance(role, str) and role and name:
            pairs[(role, name)] += 1
    return pairs


def _license_value(case: Any, license_type: str) -> str | None:
    analysis = _mapping(_mapping(case).get("license_analysis"))
    entry = _mapping(analysis.get(license_type))
    return _normalize_text(entry.get("value"))


def _citation_quotes(value: Any) -> list[str]:
    quotes: list[str] = []
    if isinstance(value, dict):
        for key, child in value.items():
            if key == "evidence_quote" and isinstance(child, str) and child.strip():
                quotes.append(child)
            else:
                quotes.extend(_citation_quotes(child))
    elif isinstance(value, list):
        for child in value:
            quotes.extend(_citation_quotes(child))
    return quotes


def _rate(numerator: int, denominator: int) -> float:
    return numerator / denominator if denominator else 0.0


def _case_result(case_input: Any, index: int) -> dict[str, Any]:
    case_input = _mapping(case_input)
    gold_case = _mapping(case_input.get("gold"))
    prediction = _mapping(case_input.get("prediction"))
    predicted_case = _mapping(prediction.get("case"))
    if not predicted_case and isinstance(prediction.get("components"), list):
        predicted_case = prediction

    gold_pairs = _lineage_pairs(gold_case)
    predicted_pairs = _lineage_pairs(predicted_case)
    true_positive = sum((gold_pairs & predicted_pairs).values())
    false_positive = sum((predicted_pairs - gold_pairs).values())
    false_negative = sum((gold_pairs - predicted_pairs).values())
    precision_denominator = true_positive + false_positive
    recall_denominator = true_positive + false_negative
    f1_denominator = 2 * true_positive + false_positive + false_negative

    licenses: dict[str, dict[str, Any]] = {}
    for license_type in ("declared_license", "upstream_license"):
        expected = _license_value(gold_case, license_type)
        actual = _license_value(predicted_case, license_type)
        evaluated = expected is not None
        correct = evaluated and expected == actual
        licenses[license_type] = {
            "expected": expected,
            "predicted": actual,
            "evaluated": evaluated,
            "correct": correct,
            "numerator": int(correct),
            "denominator": int(evaluated),
        }

    gold_status = _mapping(gold_case.get("gold")).get("expected_status")
    policy_prediction = prediction.get("policy")
    if isinstance(policy_prediction, str):
        predicted_status = policy_prediction
    else:
        predicted_status = _mapping(policy_prediction).get("status")
    if predicted_status is None:
        predicted_status = _mapping(predicted_case.get("policy")).get("automated_status")
    policy_evaluated = isinstance(gold_status, str)
    status_correct = policy_evaluated and gold_status == predicted_status
    unsafe_approval = (
        isinstance(gold_status, str)
        and gold_status in _UNSAFE_GOLD_STATUSES
        and isinstance(predicted_status, str)
        and predicted_status in _APPROVED_STATUSES
    )

    source_text = case_input.get("source_text")
    if not isinstance(source_text, str):
        source_text = prediction.get("source_text")
    citation_quotes = _citation_quotes(predicted_case)
    normalized_source = _normalize_text(source_text)
    citation_denominator = len(citation_quotes) if normalized_source is not None else 0
    citation_numerator = (
        sum(_normalize_text(quote) in normalized_source for quote in citation_quotes)
        if normalized_source is not None
        else 0
    )

    return {
        "case_id": gold_case.get("case_id", str(index)),
        "lineage": {
            "true_positive": true_positive,
            "false_positive": false_positive,
            "false_negative": false_negative,
            "precision": _rate(true_positive, precision_denominator),
            "precision_numerator": true_positive,
            "precision_denominator": precision_denominator,
            "recall": _rate(true_positive, recall_denominator),
            "recall_numerator": true_positive,
            "recall_denominator": recall_denominator,
            "f1": _rate(2 * true_positive, f1_denominator),
            "f1_numerator": 2 * true_positive,
            "f1_denominator": f1_denominator,
        },
        "licenses": licenses,
        "policy": {
            "expected": gold_status,
            "predicted": predicted_status,
            "evaluated": policy_evaluated,
            "correct": bool(status_correct),
            "unsafe_approval": bool(unsafe_approval),
        },
        "citations": {
            "quotes": len(citation_quotes),
            "grounded": citation_numerator,
            "numerator": citation_numerator,
            "denominator": citation_denominator,
            "grounding_rate": _rate(citation_numerator, citation_denominator),
        },
    }


def evaluate_cases(cases: list[dict[str, Any]]) -> dict[str, Any]:
    """여러 사례를 평가한다.

    각 항목은 ``gold``에 fixture 전체 JSON, ``prediction``에 ``case``와
    정책 엔진 결과 ``policy``(또는 상태 문자열), 선택적으로 ``source_text``를 둔다.
    예: ``{"gold": fixture, "prediction": {"case": extracted_case,
    "policy": policy_result}, "source_text": readme}``.
    """
    details = [_case_result(case, index) for index, case in enumerate(cases)]

    lineage_tp = sum(item["lineage"]["true_positive"] for item in details)
    lineage_fp = sum(item["lineage"]["false_positive"] for item in details)
    lineage_fn = sum(item["lineage"]["false_negative"] for item in details)
    precision_denominator = lineage_tp + lineage_fp
    recall_denominator = lineage_tp + lineage_fn
    f1_denominator = 2 * lineage_tp + lineage_fp + lineage_fn

    declared_correct = sum(item["licenses"]["declared_license"]["numerator"] for item in details)
    declared_total = sum(item["licenses"]["declared_license"]["denominator"] for item in details)
    upstream_correct = sum(item["licenses"]["upstream_license"]["numerator"] for item in details)
    upstream_total = sum(item["licenses"]["upstream_license"]["denominator"] for item in details)
    citation_grounded = sum(item["citations"]["numerator"] for item in details)
    citation_total = sum(item["citations"]["denominator"] for item in details)
    policy_correct = sum(item["policy"]["correct"] for item in details)
    policy_total = sum(item["policy"]["evaluated"] for item in details)
    unsafe_approvals = sum(item["policy"]["unsafe_approval"] for item in details)

    metrics = {
        "lineage_precision": _rate(lineage_tp, precision_denominator),
        "lineage_recall": _rate(lineage_tp, recall_denominator),
        "lineage_f1": _rate(2 * lineage_tp, f1_denominator),
        "declared_license_accuracy": _rate(declared_correct, declared_total),
        "upstream_license_accuracy": _rate(upstream_correct, upstream_total),
        "citation_grounding_rate": _rate(citation_grounded, citation_total),
        "policy_status_accuracy": _rate(policy_correct, policy_total),
        "unsafe_approval_count": unsafe_approvals,
    }
    numerators = {
        "lineage_precision": lineage_tp,
        "lineage_recall": lineage_tp,
        "lineage_f1": 2 * lineage_tp,
        "declared_license_accuracy": declared_correct,
        "upstream_license_accuracy": upstream_correct,
        "citation_grounding_rate": citation_grounded,
        "policy_status_accuracy": policy_correct,
        "unsafe_approval_count": unsafe_approvals,
    }
    denominators = {
        "lineage_precision": precision_denominator,
        "lineage_recall": recall_denominator,
        "lineage_f1": f1_denominator,
        "declared_license_accuracy": declared_total,
        "upstream_license_accuracy": upstream_total,
        "citation_grounding_rate": citation_total,
        "policy_status_accuracy": policy_total,
        "unsafe_approval_count": len(details),
    }
    return {
        **metrics,
        "numerators": numerators,
        "denominators": denominators,
        "cases": details,
    }
