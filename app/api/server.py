"""Reviewer 화면(React)용 API: case JSON을 정책 엔진으로 판정해 화면에 필요한 값만 돌려준다.

- 정책 판정은 backend/policy_engine.py의 evaluate를 그대로 쓴다.
- 등록은 backend/web3_register.py의 register_settlement_rule을 그대로 쓴다.
- 입금·수령은 backend/web3_actions.py의 deposit·claim을 그대로 쓴다.

실행: uvicorn app.api.server:app --port 8000
"""

import hashlib
import json
import sys
import tempfile
from decimal import Decimal, InvalidOperation
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse
from pydantic import BaseModel

ROOT = Path(__file__).resolve().parents[2]
load_dotenv(ROOT / ".env", override=False)
sys.path.insert(0, str(ROOT / "backend"))

import policy_engine as pe  # noqa: E402
import web3_actions as wa  # noqa: E402
import web3_register as wr  # noqa: E402
import extraction_validator as ev  # noqa: E402
import hf_metadata as hm  # noqa: E402
import hf_readme as hr  # noqa: E402
import llm_extractor as le  # noqa: E402
import llm_provider as lp  # noqa: E402
from web3_register import RegisterError  # noqa: E402

FIXTURES_DIR = ROOT / "fixtures"
EXAMPLES = {
    "01_normal_paper_qa": "정상 정산",
    "02_hold_noncommercial": "상업 금지 재료",
    "03_escrow_unknown_recipient": "지갑 미확인",
    "03_review_m4xx101": "라이선스 애매",
    "04_hash_mismatch": "파일 바꿔치기",
}
# 시연 ④: 원본 파일 끝에 1바이트를 추가한 변조본의 SHA-256. 체인에 등록되어 있지 않다.
EXAMPLE_TAMPERED_HASHES = {
    "04_hash_mismatch": "b1522f09eaf32b94e8263ed3b56d65d210a4eeeb550774554138c85ab9b8151a",
}
# 시연 ③: Reviewer가 확인한 뒤 보관금을 지급할 권리자.
EXAMPLE_ESCROW_HOLDERS = {
    "03_escrow_unknown_recipient": "Holder_B",
}

app = FastAPI()


class ExtractRequest(BaseModel):
    model: str


def _extract_error(status_code: int, path: str, message: str, validation=None):
    errors = [{"path": path, "message": message}]
    return JSONResponse(
        status_code=status_code,
        content={
            "valid": False,
            "case": None,
            "validation": validation or {"valid": False, "errors": errors},
            "errors": errors,
            "policy": None,
        },
    )


@app.post("/api/extract")
def extract_model(request: ExtractRequest):
    try:
        model_id = hm.parse_model_id(request.model)
    except ValueError:
        return _extract_error(400, "model", "모델 ID 또는 Hugging Face URL 형식이 올바르지 않습니다.")

    try:
        case_draft = hm.build_case_draft(model_id)
    except Exception:
        return _extract_error(502, "metadata", "Hugging Face 모델 메타데이터를 조회하지 못했습니다.")

    try:
        readme = hr.fetch_readme(model_id)
    except Exception:
        return _extract_error(502, "readme", "Hugging Face README를 조회하지 못했습니다.")

    try:
        call_llm = lp.build_call_llm_from_env()
    except ValueError as error:
        return _extract_error(503, "llm_provider", str(error))
    except RuntimeError as error:
        return _extract_error(503, "llm_provider", str(error))

    extraction = le.extract_case_from_readme(readme, case_draft, call_llm=call_llm)
    if not extraction["valid"]:
        return JSONResponse(
            status_code=422,
            content={
                "valid": False,
                "case": None,
                "validation": {"valid": False, "errors": extraction["errors"]},
                "errors": extraction["errors"],
                "policy": None,
            },
        )

    case = extraction["case"]
    validation = ev.validate_case(case)
    if not validation["valid"]:
        return JSONResponse(
            status_code=422,
            content={
                "valid": False,
                "case": None,
                "validation": validation,
                "errors": validation["errors"],
                "policy": None,
            },
        )

    policy = pe.evaluate(case, pe.load_wallet_map())
    case["policy"]["automated_status"] = policy["status"]
    case["policy"]["reasons"] = policy["reasons"]
    return {
        "valid": True,
        "case": case,
        "validation": validation,
        "policy": {"status": policy["status"], "reasons": policy["reasons"]},
    }


def review(case, raw):
    """판정 결과와 등록 가능 여부를 화면용 한 묶음으로 만든다."""
    if not isinstance(case, dict):
        raise HTTPException(400, "모델 정보 파일 형식 오류")
    result = pe.evaluate(case, pe.load_wallet_map())
    model = pe._as_dict(case.get("input"))

    register = {"ok": False, "reason": None}
    if result["status"] not in (pe.APPROVED, pe.APPROVED_WITH_ESCROW):
        register["reason"] = "이 판정은 등록 불가"
    elif not model.get("artifact_sha256"):
        register["reason"] = "모델 파일 해시 없음 · 등록 불가"
    else:
        try:
            pe.to_contract_args(result, model["artifact_sha256"], hashlib.sha256(raw).hexdigest())
            register["ok"] = True
        except ValueError as e:
            register["reason"] = f"등록 정보 만들기 실패 · {e}"

    return {
        "model": {"id": model.get("model_id"), "url": model.get("model_url"), "hash": model.get("artifact_sha256")},
        "components": [
            {"role": c.get("role"), "name": c.get("name"), "license": c.get("license_id")}
            for c in pe._as_list(case.get("components"))
            if isinstance(c, dict)
        ],
        "result": result,
        "register": register,
    }


@app.get("/api/examples")
def list_examples():
    return [{"id": p.stem, "label": EXAMPLES.get(p.stem, p.stem)} for p in sorted(FIXTURES_DIR.glob("*.json"))]


def load_example(example_id):
    """예제 파일을 읽어 (case, 원문 바이트)를 돌려준다."""
    path = FIXTURES_DIR / f"{example_id}.json"
    if path.parent != FIXTURES_DIR or not path.is_file():
        raise HTTPException(404, "예제 없음")
    raw = path.read_bytes()
    case = json.loads(raw)
    return case, raw


def register(raw):
    """case 바이트를 임시 파일로 넘겨 등록한다. 화면에 필요한 값만 돌려준다."""
    with tempfile.NamedTemporaryFile(suffix=".json") as f:
        f.write(raw)
        f.flush()
        result = wr.register_settlement_rule(f.name)
    data = {k: result[k] for k in ("ok", "tx_hash", "etherscan_url", "error_code", "error_message", "contract_address")}
    data["evidence_changed"] = bool(result["onchain_evidence_hash"]) and result["onchain_evidence_hash"] != result["evidence_hash"]
    return data


def eth(wei):
    return format((Decimal(wei) / 10**18).normalize(), "f")


def to_wei(amount_eth):
    """ETH 문자열을 wei로 바꾼다. 숫자가 아니거나 wei로 나눠떨어지지 않으면 None."""
    try:
        wei = Decimal(amount_eth) * 10**18
    except InvalidOperation:
        return None
    return int(wei) if wei.is_finite() and wei == wei.to_integral_value() else None


def tx_result(result):
    return {k: result.get(k) for k in ("ok", "status", "tx_hash", "etherscan_url", "error_code", "error_message")}


def deposit(case, amount_eth, model_hash=None):
    """승인된 모델만 입금한다. 승인되지 않았거나 해시가 없으면 네트워크에 접속하지 않고 거부한다.

    model_hash를 주면 case의 해시 대신 그 해시로 입금한다(바꿔치기 시연).
    """
    if not isinstance(case, dict):
        raise HTTPException(400, "모델 정보 파일 형식 오류")
    status =pe.evaluate(case, pe.load_wallet_map())["status"]
    model_hash = model_hash or pe._as_dict(case.get("input")).get("artifact_sha256")
    if status not in (pe.APPROVED, pe.APPROVED_WITH_ESCROW) or not model_hash:
        return tx_result({"ok": False, "status": wa.REJECTED, "error_code": "NOT_APPROVED", "error_message": "승인되지 않은 모델"})
    wei = to_wei(amount_eth)
    if wei is None:
        return tx_result({"ok": False, "error_code": "INVALID_AMOUNT", "error_message": "입금액 형식 오류"})
    return tx_result(wa.deposit(model_hash, wei, send=True))


def settlement(model_hash):
    """체인에 기록된 분배 대상과 각자 받을 금액을 읽는다. 전송은 하지 않는다."""
    try:
        model_hash = wa._model_hash(model_hash)
        key = bytes.fromhex(model_hash[2:])
        settings = wr._read_settings(None, need_key=False)
        contract, _ = wr._open_contract(wr._connect(settings["SEPOLIA_RPC_URL"]), settings["CONTRACT_ADDRESS"])
        rule = wa._get_rule(contract, model_hash)
        holders = {wallet.lower(): name for name, wallet in wa.load_holder_addresses().items()}
        recipients = []
        for wallet in rule["recipients"] + [w for w in wa.load_holder_addresses().values() if w not in rule["recipients"]]:
            amount = wa._call(contract.functions.claimable(key, wallet), "수령 가능 금액")
            # 분배 대상이 아닌 권리자는 보관금을 지급받아 받을 돈이 있을 때만 보여 준다.
            if wallet in rule["recipients"] or amount > 0:
                recipients.append({"wallet": wallet, "holder": holders.get(wallet.lower()), "claimable_eth": eth(amount)})
    except RegisterError as e:
        return {"registered": False if e.code == "MODEL_HASH_NOT_REGISTERED" else None, "error_message": e.message}
    return {"registered": True, "escrow_eth": eth(rule["escrow_balance"]), "recipients": recipients}


def release(case, raw, holder):
    """보관금 전액을 권리자의 받을 돈으로 옮긴다. 근거 해시는 예제 파일과 수령자 주소를 묶어 만든다."""
    model_hash = case["input"]["artifact_sha256"]
    current = settlement(model_hash)
    if not current["registered"]:
        return tx_result({"ok": False, "error_code": "NOT_REGISTERED", "error_message": current["error_message"]})
    wallet = wa.load_holder_addresses()[holder]
    evidence = hashlib.sha256(raw + wallet.encode()).hexdigest()
    return tx_result(wa.release_escrow(model_hash, wallet, to_wei(current["escrow_eth"]), evidence, send=True))


@app.get("/api/examples/{example_id}")
def get_example(example_id: str):
    case, raw = load_example(example_id)
    data = review(case, raw)
    if example_id in EXAMPLE_TAMPERED_HASHES:
        data["tamper"] = {"original": data["model"]["hash"], "submitted": EXAMPLE_TAMPERED_HASHES[example_id]}
        data["register"] = {"ok": False, "reason": "원본과 다른 파일이라 등록 불가"}
    data["escrow_holder"] = EXAMPLE_ESCROW_HOLDERS.get(example_id)
    return data


@app.post("/api/examples/{example_id}/register")
def register_example(example_id: str):
    _, raw = load_example(example_id)
    if example_id in EXAMPLE_TAMPERED_HASHES:
        raise HTTPException(400, "원본과 다른 파일이라 등록 불가")
    return register(raw)


@app.post("/api/examples/{example_id}/deposit")
def deposit_example(example_id: str, amount_eth: str):
    case, _ = load_example(example_id)
    return deposit(case, amount_eth, EXAMPLE_TAMPERED_HASHES.get(example_id))


@app.post("/api/examples/{example_id}/release")
def release_example(example_id: str):
    if example_id not in EXAMPLE_ESCROW_HOLDERS:
        raise HTTPException(404, "보관금 지급 대상 아님")
    case, raw = load_example(example_id)
    return release(case, raw, EXAMPLE_ESCROW_HOLDERS[example_id])


def parse_case(raw):
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        raise HTTPException(400, "JSON 파일 읽기 실패") from None


@app.post("/api/review")
async def post_review(request: Request):
    raw = await request.body()
    return review(parse_case(raw), raw)


@app.post("/api/register")
async def register_upload(request: Request):
    return await run_in_threadpool(register, await request.body())


@app.post("/api/deposit")
async def deposit_upload(request: Request, amount_eth: str):
    return await run_in_threadpool(deposit, parse_case(await request.body()), amount_eth)


@app.get("/api/settlement/{model_hash}")
def get_settlement(model_hash: str):
    return settlement(model_hash)


@app.post("/api/settlement/{model_hash}/claim/{holder}")
def post_claim(model_hash: str, holder: str):
    return tx_result(wa.claim(model_hash, holder, send=True))
