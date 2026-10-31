"""정책 엔진: 추출 결과(case)와 지갑 매핑으로 모델 상태와 정산 비율을 계산한다.

- case는 docs/schema-v0.2.json 형식. gold 필드와 commercial_use_assessment는 읽지 않는다.
- 비율과 라이선스 분류표는 policy_config.json, 지갑 매핑은 wallet_map.json에서 읽는다.
- 컨트랙트 호출은 하지 않는다. to_contract_args는 registerSettlementRule 인자만 만든다.

실행: python3 backend/policy_engine.py <case.json> [--wallet-map <wallet_map.json>]
"""

import json
import re
import sys
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
UINT16_MAX = 2**16 - 1

POLICY_CONFIG_PATH = Path(__file__).with_name("policy_config.json")
WALLET_MAP_PATH = Path(__file__).with_name("wallet_map.json")
ADDRESS_PATTERN = re.compile(r"^0x[0-9a-fA-F]{40}$")
BYTES32_PATTERN = re.compile(r"^(0x)?[0-9a-fA-F]{64}$")
ZERO_ADDRESS = "0x" + "0" * 40


def load_policy_config(path=POLICY_CONFIG_PATH):
    """policy_config.json을 읽고 비율 합계가 10000인지 확인한다."""
    with open(path, encoding="utf-8") as f:
        config = json.load(f)
    total = sum(config["role_weights_bps"].values())
    if total != TOTAL_BPS:
        raise ValueError(f"role_weights_bps 합계가 {TOTAL_BPS}이 아님: {total}")
    return config


CONFIG = load_policy_config()
ROLE_WEIGHTS_BPS = CONFIG["role_weights_bps"]


def normalize_license(value, config=None):
    """라이선스 표기를 비교용 id로 바꾼다. 예: 'Apache License, Version 2.0' -> 'apache-2.0'."""
    if not isinstance(value, str) or not value.strip():
        return None
    config = config or CONFIG
    s = value.strip().lower()
    s = re.sub(r"[,()]", " ", s)
    s = re.sub(r"\b(license|licence|version)\b", " ", s)
    s = re.sub(r"\bv(?=\d)", "", s)
    s = re.sub(r"[\s_/]+", "-", s)
    s = re.sub(r"-{2,}", "-", s).strip("-")
    if not s:
        return None
    return config["license_aliases"].get(s, s)


def classify_license(license_id, license_name=None, config=None):
    """라이선스 id(없으면 이름)를 상업 이용 분류로 변환한다. 분류표에 없으면 unknown."""
    config = config or CONFIG
    classes = config["license_classes"]
    lid = normalize_license(license_id, config) or normalize_license(license_name, config)
    if not lid:
        return UNKNOWN

    # 비상업 판정을 가장 먼저 해서 cc-by-nc-*가 다른 분류로 빠지지 않게 함
    prohibited = classes["prohibited"]
    tokens = re.split(r"[-.]+", lid)
    name_pattern = re.compile(prohibited["name_pattern"], re.IGNORECASE)
    raw_texts = [t for t in (license_id, license_name) if isinstance(t, str)]
    if any(tok in tokens for tok in prohibited["tokens"]) or any(name_pattern.search(t) for t in raw_texts):
        return PROHIBITED

    conditional = classes["conditional"]
    if lid in conditional["ids"] or any(sub in lid for sub in conditional["substrings"]):
        return CONDITIONAL

    allowed = classes["allowed"]
    if lid in allowed["ids"] or any(lid.startswith(p) for p in allowed["prefixes"]):
        return ALLOWED
    return UNKNOWN


def load_wallet_map(path=WALLET_MAP_PATH):
    """wallet_map.json을 {재료 이름: 지갑 주소}로 읽는다. placeholder(null)는 제외한다."""
    with open(path, encoding="utf-8") as f:
        materials = json.load(f)["materials"]
    return {name: entry["wallet"] for name, entry in materials.items() if entry}


def _as_dict(value):
    return value if isinstance(value, dict) else {}


def _as_list(value):
    return value if isinstance(value, list) else []


def _evidence_entry(source):
    """근거 dict에서 인용·위치·출처만 꺼낸다. 인용이 비어 있으면 None."""
    source = _as_dict(source)
    quote = source.get("evidence_quote")
    if not isinstance(quote, str) or not quote.strip():
        return None
    return {
        "evidence_quote": quote,
        "evidence_location": source.get("evidence_location"),
        "source_url": source.get("source_url"),
    }


def _component_evidence(component):
    entries = (_evidence_entry(ev) for ev in _as_list(component.get("evidence")))
    return [e for e in entries if e]


def _reason(message, target=None, evidence=None):
    return {"message": message, "target": target, "evidence": evidence or []}


def _wallet_for(name, wallet_map):
    if not isinstance(name, str):
        return None
    wallet = wallet_map.get(name)
    if isinstance(wallet, str) and ADDRESS_PATTERN.match(wallet) and wallet.lower() != ZERO_ADDRESS:
        return wallet
    return None


def _decide_status(case, wallet_map, config):
    raw_components = case.get("components")
    components = _as_list(raw_components)
    analysis = _as_dict(case.get("license_analysis"))
    role_weights = config["role_weights_bps"]

    hold_reasons = []
    review_reasons = []
    escrow_reasons = []

    if raw_components is not None and not isinstance(raw_components, list):
        review_reasons.append(_reason("components 형식 오류(목록이 아님)"))
    elif not components:
        review_reasons.append(_reason("재료 목록 없음"))

    for index, raw in enumerate(components):
        if not isinstance(raw, dict):
            review_reasons.append(_reason(f"components[{index}] 형식 오류", f"components[{index}]"))
            continue
        name = raw.get("name")
        role = raw.get("role")
        target = f"{role} {name}"
        evidence = _component_evidence(raw)
        license_id = raw.get("license_id")
        category = classify_license(license_id, raw.get("license_name"), config)

        if category == PROHIBITED:
            hold_reasons.append(_reason(f"상업 이용 금지 라이선스({license_id})", target, evidence))
        elif category == UNKNOWN:
            review_reasons.append(_reason(f"라이선스 불명({license_id or '값 없음'})", target, evidence))
        if not evidence:
            review_reasons.append(_reason("근거 인용 없음", target))
        if not isinstance(name, str) or not name.strip():
            review_reasons.append(_reason("재료 이름 없음", target, evidence))
        if role not in role_weights:
            review_reasons.append(_reason(f"비율 규칙이 없는 역할({role})", target, evidence))
        if not _wallet_for(name, wallet_map):
            escrow_reasons.append(_reason("수령 지갑 미확인, 몫을 escrow로 보관", target, evidence))

    license_evidence = []
    for key, title in (("declared_license", "선언 라이선스"), ("upstream_license", "상위 라이선스")):
        entry = _as_dict(analysis.get(key))
        value = entry.get("value")
        evidence = [e for e in [_evidence_entry(entry)] if e]
        license_evidence += evidence
        category = classify_license(value, config=config)
        if category == PROHIBITED:
            hold_reasons.append(_reason(f"상업 이용 금지 라이선스({value})", title, evidence))
        elif category == UNKNOWN:
            review_reasons.append(_reason(f"라이선스 불명({value or '값 없음'})", title, evidence))

    mismatch = analysis.get("license_metadata_mismatch")
    if mismatch is True:
        review_reasons.append(_reason("선언 라이선스와 상위 라이선스 불일치", "license_analysis", license_evidence))
    elif mismatch is not False:
        review_reasons.append(_reason("선언·상위 라이선스 불일치 여부 미확인", "license_analysis", license_evidence))

    if hold_reasons:
        return HOLD, hold_reasons
    if review_reasons:
        return REVIEW, review_reasons
    if escrow_reasons:
        return APPROVED_WITH_ESCROW, escrow_reasons
    approved = [
        _reason("라이선스·근거·수령 지갑 확인됨", f"{c.get('role')} {c.get('name')}", _component_evidence(c))
        for c in components
    ]
    return APPROVED, approved


def _compute_shares(components, wallet_map, config):
    """역할별 고정 비율을 재료에 나누고, 지갑이 없거나 빠진 역할의 몫은 escrow로 보낸다."""
    role_weights = config["role_weights_bps"]
    recipients = []
    escrow_bps = 0
    by_role = {role: [] for role in role_weights}
    for comp in components:
        if isinstance(comp, dict) and comp.get("role") in by_role:
            by_role[comp["role"]].append(comp)

    for role, weight in role_weights.items():
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


def evaluate(case: dict, wallet_map: dict, config: dict = None) -> dict:
    """모델 상태, 판정 이유(근거 포함), 수령자별 비율, escrow 비율을 반환한다."""
    config = config or CONFIG
    case = _as_dict(case)
    wallet_map = _as_dict(wallet_map)
    status, reasons = _decide_status(case, wallet_map, config)
    recipients, escrow_bps = _compute_shares(_as_list(case.get("components")), wallet_map, config)
    total_bps = sum(r["share_bps"] for r in recipients) + escrow_bps
    assert total_bps == TOTAL_BPS, total_bps
    return {
        "status": status,
        "reasons": reasons,
        "recipients": recipients,
        "escrow_bps": escrow_bps,
        "total_bps": total_bps,
    }


def _normalize_bytes32(value, label):
    if not isinstance(value, str) or not BYTES32_PATTERN.match(value):
        raise ValueError(f"{label} 형식 오류(32바이트 hex 필요): {value!r}")
    value = value.lower()
    value = value if value.startswith("0x") else "0x" + value
    if int(value, 16) == 0:
        raise ValueError(f"{label}이 0임")
    return value


def to_contract_args(result: dict, model_hash: str, evidence_hash: str) -> dict:
    """evaluate 결과를 registerSettlementRule 인자로 바꾼다. 컨트랙트의 require 조건을 미리 검증한다.

    반환 키 순서는 컨트랙트 인자 순서와 같다:
    registerSettlementRule(modelHash, recipients, sharesBps, escrowBps, evidenceHash)
    주소 체크섬(EIP-55)은 검증하지 않는다(keccak이 표준 라이브러리에 없음). web3 쪽에서 변환할 때 확인해야 함.
    """
    status = result.get("status")
    if status not in (APPROVED, APPROVED_WITH_ESCROW):
        raise ValueError(f"등록할 수 없는 상태: {status}")

    recipients = []
    shares = []
    seen = set()
    for entry in result.get("recipients") or []:
        wallet = entry.get("wallet")
        share = entry.get("share_bps")
        if not isinstance(wallet, str) or not ADDRESS_PATTERN.match(wallet):
            raise ValueError(f"주소 형식 오류: {wallet!r}")
        if wallet.lower() == ZERO_ADDRESS:
            raise ValueError("0 주소는 수령자가 될 수 없음")
        if wallet.lower() in seen:
            raise ValueError(f"중복 수령자: {wallet}")
        if not isinstance(share, int) or isinstance(share, bool) or not 0 < share <= UINT16_MAX:
            raise ValueError(f"비율 오류({wallet}): {share!r}")
        seen.add(wallet.lower())
        recipients.append(wallet)
        shares.append(share)

    if not recipients:
        raise ValueError("수령자가 없음")
    escrow_bps = result.get("escrow_bps")
    if not isinstance(escrow_bps, int) or isinstance(escrow_bps, bool) or not 0 <= escrow_bps <= UINT16_MAX:
        raise ValueError(f"escrow_bps 오류: {escrow_bps!r}")
    total = sum(shares) + escrow_bps
    if total != TOTAL_BPS:
        raise ValueError(f"비율 합계가 {TOTAL_BPS}이 아님: {total}")

    return {
        "modelHash": _normalize_bytes32(model_hash, "modelHash"),
        "recipients": recipients,
        "sharesBps": shares,
        "escrowBps": escrow_bps,
        "evidenceHash": _normalize_bytes32(evidence_hash, "evidenceHash"),
    }


def format_result(result: dict, model_id=None) -> str:
    """evaluate 결과를 사람이 읽기 좋은 텍스트로 만든다."""
    lines = []
    if model_id:
        lines.append(f"모델: {model_id}")
    lines.append(f"상태: {result['status']}")
    lines.append("")
    lines.append("이유:")
    for reason in result["reasons"]:
        target = f"[{reason['target']}] " if reason.get("target") else ""
        lines.append(f"  - {target}{reason['message']}")
        for ev in reason.get("evidence") or []:
            lines.append(f"      근거: \"{ev['evidence_quote']}\"")
            where = ", ".join(str(v) for v in (ev.get("evidence_location"), ev.get("source_url")) if v)
            if where:
                lines.append(f"            ({where})")
    lines.append("")
    lines.append("수령자:")
    name_width = max([len(str(r["name"])) for r in result["recipients"]] + [4])

    def row(role, name, wallet, bps):
        return f"  {role:<10} {name:<{name_width}}  {wallet:<42}  {bps:>5} bps ({bps / 100:g}%)"

    for r in result["recipients"]:
        lines.append(row(r["role"], str(r["name"]), r["wallet"], r["share_bps"]))
    if not result["recipients"]:
        lines.append("  (매핑된 수령자 없음)")
    lines.append(row("escrow", "", "", result["escrow_bps"]))
    lines.append(row("total", "", "", result["total_bps"]))
    return "\n".join(lines)


def main(argv=None):
    import argparse

    parser = argparse.ArgumentParser(description="정책 엔진으로 case JSON을 판정한다.")
    parser.add_argument("case", help="schema v0.2 case JSON 경로")
    parser.add_argument("--wallet-map", default=str(WALLET_MAP_PATH), help="wallet_map.json 경로")
    args = parser.parse_args(argv)

    try:
        with open(args.case, encoding="utf-8") as f:
            case = json.load(f)
        wallet_map = load_wallet_map(args.wallet_map)
    except (OSError, json.JSONDecodeError, KeyError) as e:
        print(f"입력 파일을 읽을 수 없음: {e}", file=sys.stderr)
        return 2

    result = evaluate(case, wallet_map)
    model_id = _as_dict(_as_dict(case).get("input")).get("model_id")
    print(format_result(result, model_id))
    return 0


if __name__ == "__main__":
    sys.exit(main())
