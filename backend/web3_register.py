"""정책 엔진 결과를 Sepolia 컨트랙트의 registerSettlementRule로 등록한다 (B 파트, 초안).

흐름: case 파일 + wallet_map -> evaluate -> 상태 확인 -> to_contract_args
      -> .env 설정 읽기 -> getSettlementRule로 기존 등록 확인 -> Reviewer 서명·전송 -> 영수증 대기

- modelHash: case의 input.artifact_sha256 (모델 파일 SHA-256)
- evidenceHash: case 파일 바이트 전체의 SHA-256 (파일 내용이 1바이트라도 바뀌면 값이 달라짐)
- HOLD/REVIEW와 인자 오류는 .env를 읽거나 네트워크에 접속하기 전에 차단한다.
- 개인키와 RPC URL은 반환값·에러 메시지·로그에 넣지 않는다. 네트워크 예외 원문도 URL이 섞일 수 있어 넣지 않는다.
- 서버(Streamlit)에 Reviewer 키를 두는 것은 시연용 단순화이며 한계로 명시해야 함.

실행 (기본은 조회만, 전송은 --send를 줄 때만):
  python3 backend/web3_register.py fixtures/03_escrow_unknown_recipient.json
  python3 backend/web3_register.py fixtures/03_escrow_unknown_recipient.json --send

필요 패키지: web3, python-dotenv (dotenv가 없으면 환경변수만 사용)
"""

import hashlib
import json
import os
import re
import sys
from pathlib import Path

import policy_engine as pe

SEPOLIA_CHAIN_ID = 11155111
ETHERSCAN_BASE = "https://sepolia.etherscan.io"
ABI_PATH = Path(__file__).with_name("abi.json")
DOTENV_PATH = Path(__file__).resolve().parent.parent / ".env"
RECEIPT_TIMEOUT_SECONDS = 180
REGISTRABLE_STATUSES = (pe.APPROVED, pe.APPROVED_WITH_ESCROW)

# 컨트랙트 revert 문자열 -> 발표용 한국어 문장
REVERT_MESSAGES = {
    "ONLY_REVIEWER": "정산 규칙은 Reviewer 지갑만 등록할 수 있습니다.",
    "RULE_ALREADY_REGISTERED": "이 모델 해시에는 이미 정산 규칙이 등록되어 있어 다시 등록할 수 없습니다.",
    "INVALID_MODEL_HASH": "모델 해시가 비어 있어 등록할 수 없습니다.",
    "INVALID_EVIDENCE_HASH": "근거 해시가 비어 있어 등록할 수 없습니다.",
    "NO_RECIPIENTS": "수령자가 한 명도 없어 등록할 수 없습니다.",
    "LENGTH_MISMATCH": "수령자 수와 비율 수가 맞지 않습니다.",
    "INVALID_RECIPIENT": "수령자 주소가 비어 있습니다(0 주소).",
    "INVALID_SHARE": "비율이 0인 수령자가 있습니다.",
    "DUPLICATE_RECIPIENT": "같은 지갑이 수령자 목록에 두 번 들어 있습니다.",
    "SHARES_MUST_EQUAL_100_PERCENT": "수령자 비율과 에스크로 비율의 합이 100%가 아닙니다.",
    "ONLY_OPERATOR": "입금은 Operator 지갑만 할 수 있습니다.",
    "MODEL_HASH_NOT_REGISTERED": "등록되지 않은 모델 해시입니다. 변조된 파일이거나 아직 정산 규칙이 없는 모델입니다.",
    "ZERO_DEPOSIT": "입금액이 0입니다.",
    "NOTHING_TO_CLAIM": "수령할 금액이 없습니다.",
    "TRANSFER_FAILED": "송금에 실패했습니다.",
    "REENTRANCY_BLOCKED": "재진입 시도가 차단되었습니다.",
    "INVALID_AMOUNT": "금액이 0입니다.",
    "INSUFFICIENT_ESCROW": "에스크로 잔액이 부족합니다.",
    "INVALID_RELEASE_EVIDENCE_HASH": "에스크로 해제 근거 해시가 비어 있습니다.",
    "USE_DEPOSIT": "컨트랙트로 직접 송금할 수 없습니다. deposit을 사용해야 합니다.",
}
REVERT_PATTERN = re.compile(r"\b(" + "|".join(sorted(REVERT_MESSAGES, key=len, reverse=True)) + r")\b")


class RegisterError(Exception):
    """사용자에게 보여줄 오류 코드와 한국어 메시지를 담는다."""

    def __init__(self, code, message):
        super().__init__(code)
        self.code = code
        self.message = message


def revert_to_message(error):
    """예외 문자열에서 알려진 revert 코드만 찾아 (code, 한국어 문장)을 돌려준다. 원문은 돌려주지 않는다."""
    match = REVERT_PATTERN.search(str(error))
    if match:
        return match.group(1), REVERT_MESSAGES[match.group(1)]
    return "UNKNOWN_REVERT", "컨트랙트가 요청을 거부했습니다(원인 미확인)."


def _result(**fields):
    base = {
        "ok": False,
        "status": None,
        "tx_hash": None,
        "etherscan_url": None,
        "contract_address": None,
        "error_code": None,
        "error_message": None,
        "model_hash": None,
        "evidence_hash": None,
        "onchain_evidence_hash": None,
    }
    base.update(fields)
    return base


def load_abi(path=ABI_PATH):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _checksum(address):
    from web3 import Web3

    return Web3.to_checksum_address(address)


def _to_hex(value):
    if isinstance(value, (bytes, bytearray)):
        return "0x" + bytes(value).hex()
    value = str(value)
    return value if value.startswith("0x") else "0x" + value


def _read_settings(env, need_key=True, key_name="REVIEWER_PRIVATE_KEY"):
    """설정을 읽는다. 값은 반환하지만 메시지에는 변수 이름만 쓴다. key_name은 서명에 쓸 키 변수 이름."""
    if env is None:
        try:
            from dotenv import load_dotenv

            load_dotenv(DOTENV_PATH, override=False)
        except ImportError:
            pass
        env = os.environ
    names = ["SEPOLIA_RPC_URL", "CONTRACT_ADDRESS"] + ([key_name] if need_key else [])
    missing = [n for n in names if not (env.get(n) or "").strip()]
    if missing:
        raise RegisterError("MISSING_CONFIG", f".env에 {', '.join(missing)} 값이 없습니다.")
    return {n: env[n].strip() for n in names}


def _connect(rpc_url):
    try:
        from web3 import Web3
    except ImportError:
        raise RegisterError("WEB3_NOT_INSTALLED", "web3 패키지가 설치되어 있지 않습니다.") from None
    return Web3(Web3.HTTPProvider(rpc_url, request_kwargs={"timeout": 20}))


def _load_account(private_key, role="Reviewer"):
    try:
        from eth_account import Account

        return Account.from_key(private_key)
    except Exception:
        # 예외 원문에 키 일부가 섞일 수 있어 버림
        raise RegisterError("INVALID_KEY", f"{role} 개인키 형식이 올바르지 않습니다.") from None


def _open_contract(w3, contract_address):
    """체인·컨트랙트를 확인하고 컨트랙트 객체를 돌려준다. 메인넷 등 다른 체인이면 중단한다."""
    try:
        chain_id = w3.eth.chain_id
    except Exception:
        raise RegisterError("NETWORK_ERROR", "Sepolia 노드에 연결하지 못했습니다. RPC 설정과 네트워크를 확인하세요.") from None
    if chain_id != SEPOLIA_CHAIN_ID:
        raise RegisterError("WRONG_NETWORK", f"Sepolia가 아닌 네트워크입니다(chain id {chain_id}). 전송하지 않았습니다.")
    try:
        address = _checksum(contract_address)
    except Exception:
        raise RegisterError("INVALID_CONTRACT_ADDRESS", "CONTRACT_ADDRESS 형식이 올바르지 않습니다.") from None
    try:
        code = w3.eth.get_code(address)
    except Exception:
        raise RegisterError("NETWORK_ERROR", "컨트랙트 정보를 조회하지 못했습니다.") from None
    if not code:
        raise RegisterError("CONTRACT_NOT_FOUND", "CONTRACT_ADDRESS에 배포된 컨트랙트가 없습니다.")
    return w3.eth.contract(address=address, abi=load_abi()), address


def _registered_evidence(contract, model_hash):
    """등록돼 있으면 온체인 evidenceHash를, 아니면 None을 돌려준다."""
    try:
        rule = contract.functions.getSettlementRule(bytes.fromhex(model_hash[2:])).call()
    except Exception:
        raise RegisterError("NETWORK_ERROR", "기존 등록 여부를 조회하지 못했습니다.") from None
    registered, evidence_hash = rule[0], rule[3]
    return _to_hex(evidence_hash).lower() if registered else None


def _already_registered(evidence_hash, onchain):
    if onchain == evidence_hash:
        message = "이미 같은 근거로 등록된 모델입니다. 새로 전송하지 않았습니다."
    else:
        message = (
            "이미 등록된 모델입니다. 새로 전송하지 않았습니다. "
            "다만 등록 당시 근거 해시가 지금 case 파일의 해시와 달라, 등록 이후 근거 파일이 바뀌었을 수 있습니다."
        )
    return RegisterError("ALREADY_REGISTERED", message)


class _Blocked(Exception):
    def __init__(self, error, status=None, evidence_hash=None):
        super().__init__(error.code)
        self.error = error
        self.status = status
        self.evidence_hash = evidence_hash


def register_settlement_rule(case_path, wallet_map=None, *, send=True, env=None, w3=None,
                             receipt_timeout=RECEIPT_TIMEOUT_SECONDS) -> dict:
    """case 파일을 판정하고, 등록 가능하면 Reviewer 키로 registerSettlementRule을 전송한다.

    send=False면 기존 등록 여부만 조회한다(개인키를 읽지 않음).
    env, w3는 테스트용 주입 지점이다. 기본값은 .env와 SEPOLIA_RPC_URL로 만든 연결.
    """
    # 1) 네트워크·키 없이 판정과 인자 검증
    try:
        status, args = _prepare_checked(case_path, wallet_map)
    except _Blocked as blocked:
        return _result(status=blocked.status, error_code=blocked.error.code, error_message=blocked.error.message,
                       evidence_hash=blocked.evidence_hash)
    out = {"status": status, "model_hash": args["modelHash"], "evidence_hash": args["evidenceHash"]}

    # 2) 설정 읽기와 연결
    try:
        settings = _read_settings(env, need_key=send)
        if w3 is None:
            w3 = _connect(settings["SEPOLIA_RPC_URL"])
        contract, address = _open_contract(w3, settings["CONTRACT_ADDRESS"])
        out["contract_address"] = address

        # 3) 이미 등록됐으면 전송하지 않음
        onchain = _registered_evidence(contract, args["modelHash"])
        if onchain:
            err = _already_registered(args["evidenceHash"], onchain)
            return _result(**out, onchain_evidence_hash=onchain, error_code=err.code, error_message=err.message)
        if not send:
            return _result(**out, ok=True, error_message="아직 등록되지 않은 모델입니다(조회만 함).")

        account = _load_account(settings.pop("REVIEWER_PRIVATE_KEY"))
        tx_hash = _send_register(w3, contract, account, args)
    except RegisterError as e:
        return _result(**out, error_code=e.code, error_message=e.message)

    out["tx_hash"] = tx_hash
    out["etherscan_url"] = f"{ETHERSCAN_BASE}/tx/{tx_hash}"

    # 4) 영수증 대기
    try:
        receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=receipt_timeout)
    except Exception:
        return _result(**out, error_code="TX_PENDING",
                       error_message="전송은 됐지만 제한 시간 안에 확정되지 않았습니다. Etherscan에서 상태를 확인하세요.")
    if receipt["status"] != 1:
        return _result(**out, error_code="TX_FAILED",
                       error_message="트랜잭션이 블록에 포함됐지만 실패했습니다. Etherscan에서 원인을 확인하세요.")
    return _result(**out, ok=True)


def _prepare_checked(case_path, wallet_map):
    """네트워크 없이 할 수 있는 검증을 끝내고 (status, 컨트랙트 인자)를 돌려준다. 실패하면 _Blocked."""
    try:
        raw = Path(case_path).read_bytes()
        case = json.loads(raw)
    except (OSError, ValueError):
        raise _Blocked(RegisterError("INVALID_CASE", "case 파일을 읽을 수 없습니다.")) from None
    evidence_hash = "0x" + hashlib.sha256(raw).hexdigest()

    result = pe.evaluate(case, wallet_map if wallet_map is not None else pe.load_wallet_map())
    status = result["status"]

    def blocked(code, message):
        return _Blocked(RegisterError(code, message), status, evidence_hash)

    if status not in REGISTRABLE_STATUSES:
        reasons = "; ".join(list(dict.fromkeys(r["message"] for r in result["reasons"]))[:3])
        raise blocked("BLOCKED_BY_POLICY", f"{status} 상태라 등록하지 않습니다. 이유: {reasons}")

    model_hash = pe._as_dict(pe._as_dict(case).get("input")).get("artifact_sha256")
    if not isinstance(model_hash, str) or not model_hash.strip():
        raise blocked("MISSING_MODEL_HASH", "case의 input.artifact_sha256이 비어 있어 등록할 수 없습니다.")
    try:
        args = pe.to_contract_args(result, model_hash.strip(), evidence_hash)
    except ValueError as e:
        raise blocked("INVALID_ARGS", f"컨트랙트 인자 검증 실패: {e}") from None
    try:
        args["recipients"] = [_checksum(a) for a in args["recipients"]]
    except ImportError:
        raise blocked("WEB3_NOT_INSTALLED", "web3 패키지가 설치되어 있지 않습니다.") from None
    except ValueError:
        raise blocked("INVALID_ARGS", "수령자 주소의 체크섬이 올바르지 않습니다.") from None
    return status, args


def _send_register(w3, contract, account, args):
    """Reviewer 확인 -> 트랜잭션 생성(가스 추정 포함) -> 서명 -> 전송. tx 해시를 돌려준다."""
    try:
        reviewer = contract.functions.reviewer().call()
    except Exception:
        raise RegisterError("NETWORK_ERROR", "컨트랙트의 Reviewer 주소를 조회하지 못했습니다.") from None
    if str(reviewer).lower() != account.address.lower():
        raise RegisterError("ONLY_REVIEWER", REVERT_MESSAGES["ONLY_REVIEWER"] + " .env의 Reviewer 키가 배포 때의 Reviewer와 다릅니다.")

    fn = contract.functions.registerSettlementRule(
        bytes.fromhex(args["modelHash"][2:]),
        args["recipients"],
        args["sharesBps"],
        args["escrowBps"],
        bytes.fromhex(args["evidenceHash"][2:]),
    )
    try:
        tx = fn.build_transaction({
            "from": account.address,
            "nonce": w3.eth.get_transaction_count(account.address),
            "chainId": SEPOLIA_CHAIN_ID,
        })
    except Exception as e:
        # 가스 추정 단계에서 revert가 나면 여기서 잡힘
        code, message = revert_to_message(e)
        if code == "UNKNOWN_REVERT":
            raise RegisterError("SIMULATION_FAILED", "전송 전 시뮬레이션에 실패했습니다. 잔액(가스비)과 네트워크를 확인하세요.") from None
        raise RegisterError(code, message) from None

    signed = account.sign_transaction(tx)
    raw = getattr(signed, "raw_transaction", None) or getattr(signed, "rawTransaction")
    try:
        sent = w3.eth.send_raw_transaction(raw)
    except Exception as e:
        code, message = revert_to_message(e)
        if code == "UNKNOWN_REVERT":
            raise RegisterError("SEND_FAILED", "트랜잭션 전송에 실패했습니다. 잔액(가스비)과 nonce를 확인하세요.") from None
        raise RegisterError(code, message) from None
    return _to_hex(sent)


def format_register_result(result: dict) -> str:
    labels = [
        ("ok", "성공"), ("status", "정책 상태"), ("error_code", "코드"), ("error_message", "메시지"),
        ("contract_address", "컨트랙트"), ("model_hash", "modelHash"), ("evidence_hash", "evidenceHash"),
        ("onchain_evidence_hash", "온체인 evidenceHash"), ("tx_hash", "tx"), ("etherscan_url", "Etherscan"),
    ]
    return "\n".join(f"{label}: {result[key]}" for key, label in labels if result.get(key) is not None)


def main(argv=None):
    import argparse

    parser = argparse.ArgumentParser(description="case 파일을 판정하고 Sepolia에 정산 규칙을 등록한다.")
    parser.add_argument("case", help="schema v0.2 case JSON 경로")
    parser.add_argument("--send", action="store_true", help="실제로 트랜잭션을 전송한다(없으면 조회만)")
    args = parser.parse_args(argv)
    result = register_settlement_rule(args.case, send=args.send)
    print(format_register_result(result))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
