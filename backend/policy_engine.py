"""정책 엔진: 추출 결과(case)와 지갑 매핑으로 모델 상태와 정산 비율을 계산한다.

- case는 docs/schema-v0.2.json 형식. gold 필드와 commercial_use_assessment는 읽지 않는다.
- 컨트랙트 호출은 하지 않는다. 결과는 Reviewer 승인 화면과 registerSettlementRule 입력에 쓰인다.
"""

import json
import re
from pathlib import Path

APPROVED = "APPROVED"
APPROVED_WITH_ESCROW = "APPROVED_WITH_ESCROW"
HOLD = "HOLD"
REVIEW = "REVIEW"

ALLOWED = "allowed"
CONDITIONAL = "conditional"
PROHIBITED = "prohibited"
UNKNOWN = "unknown"

TOTAL_BPS = 10000

# 시연용 고정 비율 (미확정 안, 팀 합의 전). 합계 10000
ROLE_WEIGHTS_BPS = {
    "base_model": 4000,
    "dataset": 3000,
    "adapter": 3000,
}

# 정답표 규칙표와 같게 유지해야 함
ALLOWED_LICENSES = {"mit", "apache-2.0", "cc-by-4.0"}
CONDITIONAL_LICENSES = {"llama3.2"}
NONCOMMERCIAL_NAME_PATTERN = re.compile(r"non[\s-]?commercial", re.IGNORECASE)

WALLET_MAP_PATH = Path(__file__).with_name("wallet_map.json")
ADDRESS_PATTERN = re.compile(r"^0x[0-9a-fA-F]{40}$")


def classify_license(license_id, license_name=None):
    """라이선스 id(필요하면 이름)를 상업 이용 분류로 변환한다. 목록에 없으면 unknown."""
    if not license_id:
        return UNKNOWN
    lid = license_id.strip().lower()
    tokens = re.split(r"[-_.\s]+", lid)
    # 비상업 판정을 가장 먼저 해서 cc-by-nc-*가 다른 분류로 빠지지 않게 함
    if "nc" in tokens or (license_name and NONCOMMERCIAL_NAME_PATTERN.search(license_name)):
        return PROHIBITED
    if lid in CONDITIONAL_LICENSES or "openrail" in lid:
        return CONDITIONAL
    if lid in ALLOWED_LICENSES or lid.startswith("bsd-"):
        return ALLOWED
    return UNKNOWN


def load_wallet_map(path=WALLET_MAP_PATH):
    """wallet_map.json을 {재료 이름: 지갑 주소}로 읽는다. placeholder(null)는 제외한다."""
    with open(path, encoding="utf-8") as f:
        materials = json.load(f)["materials"]
    return {name: entry["wallet"] for name, entry in materials.items() if entry}


def _has_evidence(component):
    return any((ev.get("evidence_quote") or "").strip() for ev in component.get("evidence") or [])


def _wallet_for(name, wallet_map):
    wallet = wallet_map.get(name)
    if isinstance(wallet, str) and ADDRESS_PATTERN.match(wallet):
        return wallet
    return None


def _decide_status(case, wallet_map):
    components = case.get("components") or []
    analysis = case.get("license_analysis") or {}

    hold_reasons = []
    review_reasons = []
    escrow_reasons = []

    for comp in components:
        name = comp.get("name")
        label = f"{comp.get('role')} {name}"
        category = classify_license(comp.get("license_id"), comp.get("license_name"))
        if category == PROHIBITED:
            hold_reasons.append(f"{label}: 상업 이용 금지 라이선스({comp.get('license_id')})")
        elif category == UNKNOWN:
            review_reasons.append(f"{label}: 라이선스 불명({comp.get('license_id')})")
        if not _has_evidence(comp):
            review_reasons.append(f"{label}: 근거 인용 없음")
        if comp.get("role") not in ROLE_WEIGHTS_BPS:
            review_reasons.append(f"{label}: 비율 규칙이 없는 역할")
        if not _wallet_for(name, wallet_map):
            escrow_reasons.append(f"{label}: 수령 지갑 미확인, 몫을 escrow로 보관")

    for key, title in (("declared_license", "선언 라이선스"), ("upstream_license", "상위 라이선스")):
        value = (analysis.get(key) or {}).get("value")
        category = classify_license(value)
        if category == PROHIBITED:
            hold_reasons.append(f"{title}: 상업 이용 금지 라이선스({value})")
        elif category == UNKNOWN:
            review_reasons.append(f"{title}: 라이선스 불명({value})")

    mismatch = analysis.get("license_metadata_mismatch")
    if mismatch is True:
        review_reasons.append("선언 라이선스와 상위 라이선스 불일치")
    elif mismatch is None:
        review_reasons.append("선언·상위 라이선스 불일치 여부 미확인")

    if not components:
        review_reasons.append("재료 목록 없음")

    if hold_reasons:
        return HOLD, hold_reasons
    if review_reasons:
        return REVIEW, review_reasons
    if escrow_reasons:
        return APPROVED_WITH_ESCROW, escrow_reasons
    return APPROVED, ["모든 재료의 라이선스·근거·수령 지갑 확인됨"]


def _compute_shares(components, wallet_map):
    """역할별 고정 비율을 재료에 나누고, 지갑이 없거나 빠진 역할의 몫은 escrow로 보낸다."""
    recipients = []
    escrow_bps = 0
    by_role = {role: [] for role in ROLE_WEIGHTS_BPS}
    for comp in components:
        if comp.get("role") in by_role:
            by_role[comp["role"]].append(comp)

    for role, weight in ROLE_WEIGHTS_BPS.items():
        comps = by_role[role]
        if not comps:
            # 해당 역할의 재료가 없을 때의 처리(재정규화 vs escrow)는 미확정. 우선 escrow로 보관
            escrow_bps += weight
            continue
        share, dust = divmod(weight, len(comps))
        escrow_bps += dust
        for comp in comps:
            wallet = _wallet_for(comp.get("name"), wallet_map)
            if wallet:
                recipients.append(
                    {"role": role, "name": comp.get("name"), "wallet": wallet, "share_bps": share}
                )
            else:
                escrow_bps += share

    return recipients, escrow_bps


def evaluate(case: dict, wallet_map: dict) -> dict:
    """모델 상태, 판정 이유, 수령자별 비율, escrow 비율을 반환한다."""
    status, reasons = _decide_status(case, wallet_map)
    recipients, escrow_bps = _compute_shares(case.get("components") or [], wallet_map)
    total_bps = sum(r["share_bps"] for r in recipients) + escrow_bps
    assert total_bps == TOTAL_BPS, total_bps
    return {
        "status": status,
        "reasons": reasons,
        "recipients": recipients,
        "escrow_bps": escrow_bps,
        "total_bps": total_bps,
    }
