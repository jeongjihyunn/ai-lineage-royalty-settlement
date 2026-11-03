import re
from typing import Any


_AUTOMATED_STATUSES = {
    "APPROVED",
    "APPROVED_WITH_ESCROW",
    "HOLD",
    "REVIEW",
}
_SHA256_PATTERN = re.compile(r"[0-9a-fA-F]{64}\Z")


def validate_case(case: Any) -> dict[str, Any]:
    errors: list[dict[str, str]] = []

    def add_error(path: str, message: str) -> None:
        errors.append({"path": path, "message": message})

    if not isinstance(case, dict):
        add_error("case", "케이스는 JSON 객체여야 합니다.")
        return {"valid": False, "errors": errors}

    input_data = case.get("input")
    if not isinstance(input_data, dict):
        add_error("input.model_id", "input.model_id가 필요합니다.")
    elif not isinstance(input_data.get("model_id"), str) or not input_data["model_id"].strip():
        add_error("input.model_id", "input.model_id는 비어 있지 않은 문자열이어야 합니다.")

    if isinstance(input_data, dict) and "artifact_sha256" in input_data:
        artifact_sha256 = input_data["artifact_sha256"]
        if artifact_sha256 is not None and (
            not isinstance(artifact_sha256, str)
            or _SHA256_PATTERN.fullmatch(artifact_sha256) is None
        ):
            add_error("input.artifact_sha256", "artifact_sha256는 64자리 SHA-256 16진수여야 합니다.")

    components = case.get("components")
    if not isinstance(components, list) or not components:
        add_error("components", "components는 비어 있지 않은 배열이어야 합니다.")
    else:
        for index, component in enumerate(components):
            component_path = f"components[{index}]"
            if not isinstance(component, dict):
                add_error(component_path, "component는 객체여야 합니다.")
                continue

            for field in ("role", "name"):
                value = component.get(field)
                if not isinstance(value, str) or not value.strip():
                    add_error(f"{component_path}.{field}", f"{field}는 비어 있지 않은 문자열이어야 합니다.")

            evidence = component.get("evidence")
            if not isinstance(evidence, list):
                add_error(f"{component_path}.evidence", "evidence는 배열이어야 합니다.")
                continue

            for evidence_index, item in enumerate(evidence):
                evidence_path = f"{component_path}.evidence[{evidence_index}]"
                if not isinstance(item, dict):
                    add_error(evidence_path, "evidence 항목은 객체여야 합니다.")
                    continue
                quote = item.get("evidence_quote")
                if not isinstance(quote, str) or not quote.strip():
                    add_error(
                        f"{evidence_path}.evidence_quote",
                        "evidence_quote는 비어 있지 않은 문자열이어야 합니다.",
                    )

    policy = case.get("policy")
    if not isinstance(policy, dict):
        add_error("policy.automated_status", "policy.automated_status는 null 또는 허용된 상태여야 합니다.")
    elif "automated_status" not in policy:
        add_error("policy.automated_status", "policy.automated_status 필드가 필요합니다.")
    else:
        automated_status = policy.get("automated_status")
        if automated_status is not None and (
            not isinstance(automated_status, str)
            or automated_status not in _AUTOMATED_STATUSES
        ):
            add_error(
                "policy.automated_status",
                "automated_status는 null, APPROVED, APPROVED_WITH_ESCROW, HOLD, REVIEW 중 하나여야 합니다.",
            )

    return {"valid": not errors, "errors": errors}