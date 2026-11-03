from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen


_HF_BASE_URL = "https://huggingface.co"
_REQUEST_TIMEOUT_SECONDS = 10


def parse_model_id(model_id_or_url: str) -> str:
    """Return a validated Hugging Face owner/model identifier."""
    if not isinstance(model_id_or_url, str) or not model_id_or_url.strip():
        raise ValueError("모델 ID 또는 Hugging Face 모델 URL을 입력해야 합니다.")

    value = model_id_or_url.strip()
    if "://" in value:
        parsed_url = urlsplit(value)
        if parsed_url.scheme != "https" or parsed_url.netloc != "huggingface.co":
            raise ValueError("Hugging Face 모델 URL은 https://huggingface.co/owner/model 형식이어야 합니다.")
        if parsed_url.query or parsed_url.fragment:
            raise ValueError("모델 URL에 쿼리 문자열이나 fragment를 포함할 수 없습니다.")
        value = parsed_url.path.strip("/")

    parts = value.split("/")
    if (
        len(parts) != 2
        or any(not part or part in {".", ".."} or any(char.isspace() for char in part) for part in parts)
    ):
        raise ValueError("모델 ID는 owner/model 형식이어야 합니다.")
    return "/".join(parts)


def fetch_model_metadata(model_id: str) -> dict[str, Any]:
    """Fetch one public model record from the Hugging Face API."""
    normalized_model_id = parse_model_id(model_id)
    api_url = f"{_HF_BASE_URL}/api/models/{normalized_model_id}"
    request = Request(api_url, headers={"Accept": "application/json"})

    try:
        with urlopen(request, timeout=_REQUEST_TIMEOUT_SECONDS) as response:
            payload = response.read()
    except HTTPError as error:
        raise ValueError(f"Hugging Face API HTTP 오류({error.code}): 모델 메타데이터를 가져오지 못했습니다.") from error
    except (URLError, TimeoutError, OSError) as error:
        raise ValueError("Hugging Face API에 연결할 수 없습니다.") from error

    try:
        metadata = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("Hugging Face API 응답을 JSON으로 해석할 수 없습니다.") from error

    if not isinstance(metadata, dict):
        raise ValueError("Hugging Face API 응답 구조가 올바르지 않습니다.")
    return metadata


def _metadata_values(value: Any) -> list[str]:
    if isinstance(value, str):
        values = [value]
    elif isinstance(value, list):
        values = value
    else:
        return []
    return [item.strip() for item in values if isinstance(item, str) and item.strip()]


def _evidence(field: str, value: str, api_url: str) -> dict[str, str]:
    return {
        "evidence_quote": f"{field}: {value}",
        "evidence_location": f"Hugging Face API {field}",
        "source_url": api_url,
    }


def _component(role: str, name: str, evidence: list[dict[str, str]], **fields: Any) -> dict[str, Any]:
    return {
        "role": role,
        "name": name,
        "relationship": fields.get("relationship"),
        "publisher": fields.get("publisher"),
        "publisher_confidence": "unknown",
        "rights_holder": None,
        "rights_holder_status": "unknown",
        "license_id": fields.get("license_id"),
        "license_name": None,
        "evidence": evidence,
    }


def build_case_draft(model_id_or_url: str, checked_at: str | None = None) -> dict[str, Any]:
    """Build a case JSON draft using only public API metadata."""
    model_id = parse_model_id(model_id_or_url)
    metadata = fetch_model_metadata(model_id)
    api_url = f"{_HF_BASE_URL}/api/models/{model_id}"
    model_url = f"{_HF_BASE_URL}/{model_id}"
    card_data = metadata.get("cardData")
    if not isinstance(card_data, dict):
        card_data = {}

    license_values = _metadata_values(card_data.get("license"))
    declared_license = license_values[0] if license_values else None
    base_models = _metadata_values(card_data.get("base_model"))
    datasets = _metadata_values(card_data.get("datasets"))
    author_value = metadata.get("author")
    author = author_value.strip() if isinstance(author_value, str) and author_value.strip() else None

    adapter_evidence: list[dict[str, str]] = []
    api_model_id = metadata.get("modelId")
    if isinstance(api_model_id, str) and api_model_id.strip():
        adapter_evidence.append(_evidence("modelId", api_model_id.strip(), api_url))
    if author is not None:
        adapter_evidence.append(_evidence("author", author, api_url))
    for license_value in license_values:
        adapter_evidence.append(_evidence("cardData.license", license_value, api_url))
    for base_model in base_models:
        adapter_evidence.append(_evidence("cardData.base_model", base_model, api_url))
    for dataset in datasets:
        adapter_evidence.append(_evidence("cardData.datasets", dataset, api_url))

    components = [
        _component(
            "adapter",
            model_id,
            adapter_evidence,
            publisher=author,
            license_id=declared_license,
        )
    ]
    for base_model in base_models:
        components.append(
            _component(
                "base_model",
                base_model,
                [_evidence("cardData.base_model", base_model, api_url)],
                relationship="base_for",
            )
        )
    for dataset in datasets:
        components.append(
            _component(
                "dataset",
                dataset,
                [_evidence("cardData.datasets", dataset, api_url)],
                relationship="trained_on",
            )
        )

    if declared_license is None:
        declared_license_record = {
            "value": None,
            "evidence_quote": None,
            "evidence_location": None,
            "source_url": None,
        }
    else:
        license_evidence = _evidence("cardData.license", declared_license, api_url)
        declared_license_record = {
            "value": declared_license,
            "evidence_quote": license_evidence["evidence_quote"],
            "evidence_location": license_evidence["evidence_location"],
            "source_url": license_evidence["source_url"],
        }

    timestamp = checked_at or datetime.now(timezone.utc).date().isoformat()
    case_id = f"hf_{model_id.replace('/', '_')}_{timestamp.replace('-', '')}"

    # 메타데이터 초안은 본문 원문 인용 검증 전까지 자동 승인이나 정산에 사용하면 안 됩니다.
    return {
        "schema_version": "0.2",
        "case_id": case_id,
        "input": {
            "model_id": model_id,
            "model_url": model_url,
            "revision": None,
            "checked_at": timestamp,
            "artifact_file_name": None,
            "artifact_sha256": None,
        },
        "components": components,
        "license_analysis": {
            "declared_license": declared_license_record,
            "upstream_license": {
                "value": None,
                "evidence_quote": None,
                "evidence_location": None,
                "source_url": None,
            },
            "license_metadata_mismatch": None,
            "commercial_use_assessment": "review_required",
            "findings": [],
        },
        "policy": {"automated_status": None, "reasons": []},
        "review": {"reviewer_decision": None, "reviewer_wallet": None, "approved_at": None},
        "settlement": {"recipients": [], "escrow_bps": 0, "total_bps": 0},
        "gold": {
            "expected_status": None,
            "state_reason": None,
            "split": "dev",
            "annotators": [],
            "annotation_minutes": None,
        },
    }