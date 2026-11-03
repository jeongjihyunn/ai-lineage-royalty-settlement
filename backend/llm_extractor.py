from __future__ import annotations

import copy
import json
import re
from typing import Any, Callable

from backend.hf_readme import build_readme_url


_ALLOWED_ROLES = {"base_model", "dataset", "adapter"}


def build_extraction_prompt(readme: str, case_draft: dict[str, Any]) -> str:
    draft_json = json.dumps(case_draft, ensure_ascii=False, indent=2)
    return (
        "다음 Hugging Face README 본문에서 모델 계보와 선언 라이선스의 사실 및 근거 문장만 추출하세요.\n"
        "법적 판단, 상업적 사용 가능성 판단, 권리자 추정, 지갑 주소 추정, 비율 계산, 정책 상태 결정은 하지 마세요.\n"
        "역할은 base_model, dataset, adapter만 사용하고, README에서 직접 확인되지 않는 항목은 생략하세요.\n"
        "각 항목의 evidence_quote는 README에 있는 짧은 원문 문장을 그대로 복사하세요. 요약하거나 만들어내지 마세요.\n"
        "설명이나 Markdown 코드 펜스 없이 아래 구조의 JSON 객체 하나만 출력하세요.\n"
        '{"components":[{"role":"base_model|dataset|adapter","name":"원문에 나온 이름",'
        '"evidence_quote":"README 원문 인용"}],'
        '"declared_license":{"value":"README에 적힌 라이선스 또는 null",'
        '"evidence_quote":"라이선스가 적힌 README 원문 또는 null"}}\n'
        "찾을 수 없는 선언 라이선스는 value와 evidence_quote를 모두 null로 설정하세요.\n\n"
        "메타데이터 초안(JSON):\n"
        f"{draft_json}\n\n"
        "README 본문:\n"
        f"{readme}"
    )


def _error(path: str, message: str) -> dict[str, str]:
    return {"path": path, "message": message}


def _failure(errors: list[dict[str, str]]) -> dict[str, Any]:
    return {"valid": False, "errors": errors, "case": None}


def _normalize_whitespace(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def _parse_llm_response(response: Any, readme: str) -> tuple[dict[str, Any] | None, list[dict[str, str]]]:
    if not isinstance(response, str):
        return None, [_error("llm_response", "LLM 응답은 JSON 문자열이어야 합니다.")]

    try:
        parsed = json.loads(response)
    except (json.JSONDecodeError, TypeError):
        return None, [_error("llm_response", "LLM 응답이 올바른 JSON이 아닙니다.")]

    if not isinstance(parsed, dict):
        return None, [_error("llm_response", "LLM JSON 최상위 값은 객체여야 합니다.")]

    errors: list[dict[str, str]] = []
    extracted_components = parsed.get("components")
    if not isinstance(extracted_components, list):
        errors.append(_error("components", "LLM JSON의 components는 배열이어야 합니다."))
        extracted_components = []

    normalized_readme = _normalize_whitespace(readme)
    valid_components: list[dict[str, str]] = []
    for index, item in enumerate(extracted_components):
        path = f"components[{index}]"
        if not isinstance(item, dict):
            errors.append(_error(path, "계보 항목은 객체여야 합니다."))
            continue

        role = item.get("role")
        name = item.get("name")
        quote = item.get("evidence_quote")
        role_is_valid = isinstance(role, str) and role in _ALLOWED_ROLES
        if not role_is_valid:
            errors.append(_error(f"{path}.role", "role은 base_model, dataset, adapter 중 하나여야 합니다."))
        if not isinstance(name, str) or not name.strip():
            errors.append(_error(f"{path}.name", "name은 비어 있지 않은 문자열이어야 합니다."))
        if not isinstance(quote, str) or not quote.strip():
            errors.append(_error(f"{path}.evidence_quote", "evidence_quote는 비어 있지 않은 문자열이어야 합니다."))
        elif _normalize_whitespace(quote) not in normalized_readme:
            errors.append(_error(f"{path}.evidence_quote", "인용문이 README 본문에 없습니다."))

        if (
            role_is_valid
            and isinstance(name, str)
            and name.strip()
            and isinstance(quote, str)
            and quote.strip()
            and _normalize_whitespace(quote) in normalized_readme
        ):
            valid_components.append(
                {"role": role, "name": name.strip(), "evidence_quote": quote.strip()}
            )

    declared_license = parsed.get("declared_license")
    if not isinstance(declared_license, dict):
        errors.append(_error("declared_license", "LLM JSON의 declared_license는 객체여야 합니다."))
        declared_license = {}
    license_value = declared_license.get("value")
    license_quote = declared_license.get("evidence_quote")
    if license_value is not None and (not isinstance(license_value, str) or not license_value.strip()):
        errors.append(_error("declared_license.value", "선언 라이선스는 비어 있지 않은 문자열 또는 null이어야 합니다."))
    if license_quote is not None and (not isinstance(license_quote, str) or not license_quote.strip()):
        errors.append(_error("declared_license.evidence_quote", "라이선스 evidence_quote는 비어 있지 않은 문자열 또는 null이어야 합니다."))
    elif isinstance(license_quote, str) and _normalize_whitespace(license_quote) not in normalized_readme:
        errors.append(_error("declared_license.evidence_quote", "라이선스 인용문이 README 본문에 없습니다."))
    if (license_value is None) != (license_quote is None):
        errors.append(_error("declared_license", "라이선스 value와 evidence_quote는 함께 제공되거나 모두 null이어야 합니다."))

    if errors:
        return None, errors
    return {
        "components": valid_components,
        "declared_license": {
            "value": license_value.strip() if isinstance(license_value, str) else None,
            "evidence_quote": license_quote.strip() if isinstance(license_quote, str) else None,
        },
    }, []


def _new_component(role: str, name: str) -> dict[str, Any]:
    relationships = {"base_model": "base_for", "dataset": "trained_on", "adapter": None}
    return {
        "role": role,
        "name": name,
        "relationship": relationships[role],
        "publisher": None,
        "publisher_confidence": "unknown",
        "rights_holder": None,
        "rights_holder_status": "unknown",
        "license_id": None,
        "license_name": None,
        "evidence": [],
    }


def _apply_extraction(
    case_draft: dict[str, Any], extracted: dict[str, Any]
) -> tuple[dict[str, Any] | None, list[dict[str, str]]]:
    case = copy.deepcopy(case_draft)
    components = case.get("components")
    if not isinstance(components, list):
        return None, [_error("case.components", "메타데이터 초안의 components는 배열이어야 합니다.")]

    input_data = case.get("input")
    model_id = input_data.get("model_id") if isinstance(input_data, dict) else None
    try:
        source_url = build_readme_url(model_id) if isinstance(model_id, str) else None
    except ValueError:
        source_url = input_data.get("model_url") if isinstance(input_data, dict) else None

    for item in extracted["components"]:
        component = next(
            (
                existing
                for existing in components
                if isinstance(existing, dict)
                and existing.get("role") == item["role"]
                and existing.get("name") == item["name"]
            ),
            None,
        )
        if component is None:
            component = _new_component(item["role"], item["name"])
            components.append(component)
        evidence = component.get("evidence")
        if not isinstance(evidence, list):
            evidence = []
            component["evidence"] = evidence
        evidence.append(
            {
                "evidence_quote": item["evidence_quote"],
                "evidence_location": "README 본문",
                "source_url": source_url,
            }
        )

    license_data = extracted["declared_license"]
    if license_data["value"] is not None:
        analysis = case.setdefault("license_analysis", {})
        declared = analysis.setdefault("declared_license", {})
        declared.update(
            {
                "value": license_data["value"],
                "evidence_quote": license_data["evidence_quote"],
                "evidence_location": "README 본문",
                "source_url": source_url,
            }
        )

    license_analysis = case.setdefault("license_analysis", {})
    license_analysis["license_metadata_mismatch"] = None
    policy = case.setdefault("policy", {})
    policy["automated_status"] = None
    return case, []


def extract_case_from_readme(
    readme: str,
    case_draft: dict[str, Any],
    call_llm: Callable[[str], str],
) -> dict[str, Any]:
    if not isinstance(readme, str) or not readme.strip():
        return _failure([_error("readme", "README 본문이 비어 있습니다.")])
    if not isinstance(case_draft, dict):
        return _failure([_error("case_draft", "메타데이터 초안은 JSON 객체여야 합니다.")])
    if not callable(call_llm):
        return _failure([_error("call_llm", "LLM 호출 함수가 필요합니다.")])

    prompt = build_extraction_prompt(readme, case_draft)
    try:
        response = call_llm(prompt)
    except Exception:
        return _failure([_error("llm_response", "주입된 LLM 호출 함수 실행에 실패했습니다.")])

    extracted, errors = _parse_llm_response(response, readme)
    if errors or extracted is None:
        return _failure(errors)

    case, errors = _apply_extraction(case_draft, extracted)
    if errors or case is None:
        return _failure(errors)
    return {"valid": True, "errors": [], "case": case}