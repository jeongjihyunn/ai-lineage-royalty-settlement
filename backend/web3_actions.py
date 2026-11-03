"""입금(deposit)·수령(claim)·에스크로 해제(releaseEscrow) 함수 (B 파트, 초안).

- 반환 형식, 한국어 오류 매핑, 사전 차단(체인 id·컨트랙트 코드·권한 주소)은 web3_register를 재사용한다.
- 기본은 조회 모드(send=False): 개인키를 읽지 않고 등록 여부·잔액만 확인한다. 전송은 send=True일 때만.
- 서명 키: deposit은 OPERATOR, claim은 HOLDER_A/B/C, release_escrow는 REVIEWER (.env에서만 읽음).
- deposit 결과의 status는 입금 요청 결과(ACCEPTED/REJECTED)이며 모델 상태가 아니다.
  ACCEPTED는 전송·확정 성공, REJECTED는 컨트랙트 규칙(미등록 해시 등)으로 거부된 경우. 조회 모드·네트워크 오류는 None.

실행 (기본은 조회만, 전송은 --send를 줄 때만):
  python3 backend/web3_actions.py deposit <model_hash> <amount_wei> [--send]
  python3 backend/web3_actions.py claim <model_hash> <A|B|C> [--send]
  python3 backend/web3_actions.py release <model_hash> <recipient> <amount_wei> <release_evidence_hash> [--send]
"""

import json
import sys

import policy_engine as pe
import web3_register as wr
from web3_register import RegisterError

ACCEPTED = "ACCEPTED"
REJECTED = "REJECTED"

HOLDER_KEYS = {
    "Holder_A": "HOLDER_A_PRIVATE_KEY",
    "Holder_B": "HOLDER_B_PRIVATE_KEY",
    "Holder_C": "HOLDER_C_PRIVATE_KEY",
}


def load_holder_addresses(path=pe.WALLET_MAP_PATH):
    """wallet_map.json의 holder 표기에서 {Holder_X: 공개 주소}를 만든다. 같은 Holder가 다른 주소면 오류."""
    with open(path, encoding="utf-8") as f:
        materials = json.load(f)["materials"]
    holders = {}
    for entry in materials.values():
        if not entry or not entry.get("holder"):
            continue
        name, wallet = entry["holder"], entry["wallet"]
        if holders.setdefault(name, wallet).lower() != wallet.lower():
            raise ValueError(f"wallet_map.json에서 {name}의 주소가 서로 다름")
    return holders


def _holder_name(holder):
    """'A', 'holder_a', 'Holder_A'를 모두 'Holder_A'로 맞춘다."""
    text = str(holder or "").strip().upper()
    if text.startswith("HOLDER_"):
        text = text[len("HOLDER_"):]
    name = f"Holder_{text}"
    if name not in HOLDER_KEYS:
        raise RegisterError("INVALID_HOLDER", "Holder는 A, B, C 중 하나여야 합니다.")
    return name


def _bytes32(value, code, message):
    try:
        return pe._normalize_bytes32(value, code)
    except ValueError:
        raise RegisterError(code, message) from None


def _model_hash(value):
    return _bytes32(value, "INVALID_MODEL_HASH", "모델 해시 형식이 올바르지 않습니다(0이 아닌 32바이트 hex 필요).")


def _positive_int(value, code):
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise RegisterError(code, wr.REVERT_MESSAGES[code])
    return value


def _get_rule(contract, model_hash):
    try:
        registered, escrow_bps, escrow_balance, evidence_hash, recipients, shares = (
            contract.functions.getSettlementRule(bytes.fromhex(model_hash[2:])).call()
        )
    except Exception:
        raise RegisterError("NETWORK_ERROR", "정산 규칙을 조회하지 못했습니다.") from None
    if not registered:
        raise RegisterError("MODEL_HASH_NOT_REGISTERED", wr.REVERT_MESSAGES["MODEL_HASH_NOT_REGISTERED"])
    return {
        "escrow_bps": escrow_bps,
        "escrow_balance": escrow_balance,
        "evidence_hash": wr._to_hex(evidence_hash).lower(),
        "recipients": list(recipients),
        "shares_bps": list(shares),
    }


def _call(fn, what):
    try:
        return fn.call()
    except Exception:
        raise RegisterError("NETWORK_ERROR", f"{what}을(를) 조회하지 못했습니다.") from None


def _send_tx(w3, account, fn, value=0):
    """트랜잭션 생성(가스 추정 포함) -> 서명 -> 전송. 가스 추정에서 revert가 나면 전송하지 않는다."""
    tx_params = {
        "from": account.address,
        "nonce": w3.eth.get_transaction_count(account.address),
        "chainId": wr.SEPOLIA_CHAIN_ID,
    }
    if value:
        tx_params["value"] = value
    try:
        tx = fn.build_transaction(tx_params)
    except Exception as e:
        code, message = wr.revert_to_message(e)
        if code == "UNKNOWN_REVERT":
            raise RegisterError("SIMULATION_FAILED", "전송 전 시뮬레이션에 실패했습니다. 잔액(가스비)과 네트워크를 확인하세요.") from None
        raise RegisterError(code, message) from None

    signed = account.sign_transaction(tx)
    raw = getattr(signed, "raw_transaction", None) or getattr(signed, "rawTransaction")
    try:
        return wr._to_hex(w3.eth.send_raw_transaction(raw))
    except Exception as e:
        code, message = wr.revert_to_message(e)
        if code == "UNKNOWN_REVERT":
            raise RegisterError("SEND_FAILED", "트랜잭션 전송에 실패했습니다. 잔액(가스비)과 nonce를 확인하세요.") from None
        raise RegisterError(code, message) from None


def _wait_receipt(w3, out, receipt_timeout):
    try:
        receipt = w3.eth.wait_for_transaction_receipt(out["tx_hash"], timeout=receipt_timeout)
    except Exception:
        return wr._result(**out, error_code="TX_PENDING",
                          error_message="전송은 됐지만 제한 시간 안에 확정되지 않았습니다. Etherscan에서 상태를 확인하세요.")
    if receipt["status"] != 1:
        return wr._result(**out, error_code="TX_FAILED",
                          error_message="트랜잭션이 블록에 포함됐지만 실패했습니다. Etherscan에서 원인을 확인하세요.")
    return wr._result(**out, ok=True)


def _error(out, error, accepted_status):
    """오류 결과. deposit이면 컨트랙트 규칙 위반(revert 코드)을 REJECTED로 표시한다."""
    rejected = bool(accepted_status) and error.code in wr.REVERT_MESSAGES
    return wr._result(**dict(out, status=REJECTED if rejected else None),
                      error_code=error.code, error_message=error.message)


def _run(*, model_hash, key_name, role, validate, check, signer_ok, build, value_of, send, env, w3,
         receipt_timeout, accepted_status=None):
    """공통 흐름: 입력 검증 -> 설정·연결 -> 조회 검사 -> (send면) 키 로드·권한 확인·전송 -> 영수증.

    validate(): 네트워크 없이 입력 검증. check(contract, ctx): 조회 검사 후 details dict 반환.
    signer_ok(contract, account, ctx): 서명 주소가 맞는지 확인. build(contract, ctx): 컨트랙트 함수 객체.
    """
    out = {"status": None}
    try:
        ctx = {"model_hash": _model_hash(model_hash)}
        out["model_hash"] = ctx["model_hash"]
        ctx.update(validate())
    except RegisterError as e:
        return _error(out, e, accepted_status)

    try:
        settings = wr._read_settings(env, need_key=send, key_name=key_name)
        if w3 is None:
            w3 = wr._connect(settings["SEPOLIA_RPC_URL"])
        contract, address = wr._open_contract(w3, settings["CONTRACT_ADDRESS"])
        out["contract_address"] = address
        out["details"] = check(contract, ctx)
        if not send:
            return wr._result(**out, ok=True, error_message="조회만 했습니다. 전송하려면 send=True(--send)로 실행하세요.")

        account = wr._load_account(settings.pop(key_name), role)
        signer_ok(contract, account, ctx)
        out["tx_hash"] = _send_tx(w3, account, build(contract, ctx), value_of(ctx))
    except RegisterError as e:
        return _error(out, e, accepted_status)

    out["etherscan_url"] = f"{wr.ETHERSCAN_BASE}/tx/{out['tx_hash']}"
    result = _wait_receipt(w3, out, receipt_timeout)
    if accepted_status:
        result["status"] = accepted_status if result["ok"] else (REJECTED if result["error_code"] == "TX_FAILED" else None)
    return result


def deposit(model_hash, amount_wei, *, send=False, env=None, w3=None,
            receipt_timeout=wr.RECEIPT_TIMEOUT_SECONDS) -> dict:
    """Operator 키로 등록된 모델 해시에 테스트 ETH(wei)를 입금한다. 미등록(변조) 해시는 전송 전에 거부한다."""

    def validate():
        return {"amount": _positive_int(amount_wei, "ZERO_DEPOSIT")}

    def check(contract, ctx):
        rule = _get_rule(contract, ctx["model_hash"])
        return {"amount_wei": ctx["amount"], "escrow_bps": rule["escrow_bps"],
                "recipients": rule["recipients"], "shares_bps": rule["shares_bps"]}

    def signer_ok(contract, account, ctx):
        operator = _call(contract.functions.operator(), "Operator 주소")
        if str(operator).lower() != account.address.lower():
            raise RegisterError("ONLY_OPERATOR", wr.REVERT_MESSAGES["ONLY_OPERATOR"] + " .env의 Operator 키가 배포 때의 Operator와 다릅니다.")

    def build(contract, ctx):
        return contract.functions.deposit(bytes.fromhex(ctx["model_hash"][2:]))

    return _run(model_hash=model_hash, key_name="OPERATOR_PRIVATE_KEY", role="Operator", validate=validate,
                check=check, signer_ok=signer_ok, build=build, value_of=lambda ctx: ctx["amount"],
                send=send, env=env, w3=w3, receipt_timeout=receipt_timeout, accepted_status=ACCEPTED)


def claim(model_hash, holder, *, send=False, env=None, w3=None,
          receipt_timeout=wr.RECEIPT_TIMEOUT_SECONDS) -> dict:
    """Holder 키로 본인 몫을 수령한다. 조회 모드는 wallet_map의 공개 주소로 claimable만 확인한다."""

    def validate():
        name = _holder_name(holder)
        try:
            address = load_holder_addresses().get(name)
        except (OSError, ValueError, KeyError) as e:
            raise RegisterError("INVALID_WALLET_MAP", f"wallet_map.json을 읽을 수 없습니다: {e}") from None
        if not address:
            raise RegisterError("INVALID_HOLDER", f"wallet_map.json에 {name}의 주소가 없습니다.")
        try:
            address = wr._checksum(address)
        except ImportError:
            raise RegisterError("WEB3_NOT_INSTALLED", "web3 패키지가 설치되어 있지 않습니다.") from None
        return {"holder": name, "address": address}

    def check(contract, ctx):
        _get_rule(contract, ctx["model_hash"])
        amount = _call(contract.functions.claimable(bytes.fromhex(ctx["model_hash"][2:]), ctx["address"]), "수령 가능 금액")
        if amount <= 0:
            raise RegisterError("NOTHING_TO_CLAIM", wr.REVERT_MESSAGES["NOTHING_TO_CLAIM"])
        return {"holder": ctx["holder"], "holder_address": ctx["address"], "claimable_wei": amount}

    def signer_ok(contract, account, ctx):
        if account.address.lower() != ctx["address"].lower():
            raise RegisterError("HOLDER_KEY_MISMATCH",
                                f".env의 {HOLDER_KEYS[ctx['holder']]}가 {ctx['holder']} 주소({ctx['address']})의 키가 아닙니다.")

    def build(contract, ctx):
        return contract.functions.claim(bytes.fromhex(ctx["model_hash"][2:]))

    try:
        key_name = HOLDER_KEYS[_holder_name(holder)]
    except RegisterError as e:
        return wr._result(error_code=e.code, error_message=e.message)
    return _run(model_hash=model_hash, key_name=key_name, role="Holder", validate=validate, check=check,
                signer_ok=signer_ok, build=build, value_of=lambda ctx: 0,
                send=send, env=env, w3=w3, receipt_timeout=receipt_timeout)


def release_escrow(model_hash, recipient, amount, release_evidence_hash, *, send=False, env=None, w3=None,
                   receipt_timeout=wr.RECEIPT_TIMEOUT_SECONDS) -> dict:
    """Reviewer 키로 에스크로 몫을 recipient의 claimable로 옮긴다(직접 송금 아님)."""

    def validate():
        try:
            address = wr._checksum(recipient)
        except ImportError:
            raise RegisterError("WEB3_NOT_INSTALLED", "web3 패키지가 설치되어 있지 않습니다.") from None
        except Exception:
            raise RegisterError("INVALID_RECIPIENT", "수령자 주소 형식이 올바르지 않습니다.") from None
        if address.lower() == pe.ZERO_ADDRESS:
            raise RegisterError("INVALID_RECIPIENT", wr.REVERT_MESSAGES["INVALID_RECIPIENT"])
        return {
            "recipient": address,
            "amount": _positive_int(amount, "INVALID_AMOUNT"),
            "release_evidence_hash": _bytes32(release_evidence_hash, "INVALID_RELEASE_EVIDENCE_HASH",
                                              wr.REVERT_MESSAGES["INVALID_RELEASE_EVIDENCE_HASH"]),
        }

    def check(contract, ctx):
        rule = _get_rule(contract, ctx["model_hash"])
        if ctx["amount"] > rule["escrow_balance"]:
            raise RegisterError("INSUFFICIENT_ESCROW",
                                f"{wr.REVERT_MESSAGES['INSUFFICIENT_ESCROW']} (잔액 {rule['escrow_balance']} wei)")
        return {"recipient": ctx["recipient"], "amount_wei": ctx["amount"], "escrow_balance_wei": rule["escrow_balance"],
                "release_evidence_hash": ctx["release_evidence_hash"]}

    def signer_ok(contract, account, ctx):
        reviewer = _call(contract.functions.reviewer(), "Reviewer 주소")
        if str(reviewer).lower() != account.address.lower():
            raise RegisterError("ONLY_REVIEWER", wr.REVERT_MESSAGES["ONLY_REVIEWER"] + " .env의 Reviewer 키가 배포 때의 Reviewer와 다릅니다.")

    def build(contract, ctx):
        return contract.functions.releaseEscrow(
            bytes.fromhex(ctx["model_hash"][2:]), ctx["recipient"], ctx["amount"],
            bytes.fromhex(ctx["release_evidence_hash"][2:]),
        )

    return _run(model_hash=model_hash, key_name="REVIEWER_PRIVATE_KEY", role="Reviewer", validate=validate,
                check=check, signer_ok=signer_ok, build=build, value_of=lambda ctx: 0,
                send=send, env=env, w3=w3, receipt_timeout=receipt_timeout)


def format_action_result(result: dict) -> str:
    # status는 모델 상태가 아니라 요청 결과(ACCEPTED/REJECTED)라 라벨을 따로 붙임
    text = wr.format_register_result(dict(result, status=None))
    if result.get("status"):
        text = f"요청 결과: {result['status']}\n" + text
    for key, value in (result.get("details") or {}).items():
        text += f"\n{key}: {value}"
    return text


def main(argv=None):
    import argparse

    parser = argparse.ArgumentParser(description="입금·수령·에스크로 해제 (기본은 조회만)")
    sub = parser.add_subparsers(dest="action", required=True)
    p = sub.add_parser("deposit")
    p.add_argument("model_hash")
    p.add_argument("amount_wei", type=int)
    p = sub.add_parser("claim")
    p.add_argument("model_hash")
    p.add_argument("holder", help="A, B, C")
    p = sub.add_parser("release")
    p.add_argument("model_hash")
    p.add_argument("recipient")
    p.add_argument("amount_wei", type=int)
    p.add_argument("release_evidence_hash")
    for p in sub.choices.values():
        p.add_argument("--send", action="store_true", help="실제로 트랜잭션을 전송한다(없으면 조회만)")
    args = parser.parse_args(argv)

    if args.action == "deposit":
        result = deposit(args.model_hash, args.amount_wei, send=args.send)
    elif args.action == "claim":
        result = claim(args.model_hash, args.holder, send=args.send)
    else:
        result = release_escrow(args.model_hash, args.recipient, args.amount_wei, args.release_evidence_hash,
                                send=args.send)
    print(format_action_result(result))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
