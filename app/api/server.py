"""Reviewer 화면(React)용 API: case JSON을 정책 엔진으로 판정해 화면에 필요한 값만 돌려준다.

- 정책 판정은 backend/policy_engine.py의 evaluate를 그대로 쓴다.
- 등록은 backend/web3_register.py의 register_settlement_rule을 그대로 쓴다.

실행: uvicorn app.api.server:app --port 8000
"""

import hashlib
import json
import sys
import tempfile
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.concurrency import run_in_threadpool

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

import policy_engine as pe  # noqa: E402
import web3_register as wr  # noqa: E402

FIXTURES_DIR = ROOT / "fixtures"
EXAMPLES = {
    "01_normal_paper_qa": "정상 정산",
    "02_hold_noncommercial": "상업 금지 재료",
    "03_escrow_unknown_recipient": "지갑 미확인",
    "03_review_m4xx101": "라이선스 애매",
    "04_hash_mismatch": "파일 바꿔치기",
}
# 예제 파일에 비어 있는 모델 파일 해시. Hugging Face에 공개된 adapter_model.safetensors의 SHA-256이다.
EXAMPLE_HASHES = {
    "01_normal_paper_qa": "4ef4de62188b4b3718258d63a1a6d9ad90416aa2c578abd67fabe69a7111a352",
}

app = FastAPI()


def review(case, raw):
    """판정 결과와 등록 가능 여부를 화면용 한 묶음으로 만든다."""
    if not isinstance(case, dict):
        raise HTTPException(400, "case JSON은 객체여야 합니다.")
    result = pe.evaluate(case, pe.load_wallet_map())
    model = pe._as_dict(case.get("input"))

    register = {"ok": False, "reason": None}
    if result["status"] not in (pe.APPROVED, pe.APPROVED_WITH_ESCROW):
        register["reason"] = "이 상태에서는 정산 규칙을 등록할 수 없습니다."
    elif not model.get("artifact_sha256"):
        register["reason"] = "모델 파일 해시(input.artifact_sha256)가 없어 등록할 수 없습니다."
    else:
        try:
            pe.to_contract_args(result, model["artifact_sha256"], hashlib.sha256(raw).hexdigest())
            register["ok"] = True
        except ValueError as e:
            register["reason"] = f"등록 인자 생성 실패: {e}"

    return {
        "model": {"id": model.get("model_id"), "url": model.get("model_url")},
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
    """예제 파일을 읽어 (case, 원문 바이트)를 돌려준다. 해시가 빠진 예제는 공개된 실제 해시를 채운다."""
    path = FIXTURES_DIR / f"{example_id}.json"
    if path.parent != FIXTURES_DIR or not path.is_file():
        raise HTTPException(404, "예제를 찾을 수 없습니다.")
    raw = path.read_bytes()
    case = json.loads(raw)
    model = case.get("input")
    if example_id in EXAMPLE_HASHES and isinstance(model, dict) and not model.get("artifact_sha256"):
        model["artifact_sha256"] = EXAMPLE_HASHES[example_id]
        raw = json.dumps(case, ensure_ascii=False, indent=2).encode()
    return case, raw


def register(raw):
    """case 바이트를 임시 파일로 넘겨 등록한다. 화면에 필요한 값만 돌려준다."""
    with tempfile.NamedTemporaryFile(suffix=".json") as f:
        f.write(raw)
        f.flush()
        result = wr.register_settlement_rule(f.name)
    return {k: result[k] for k in ("ok", "tx_hash", "etherscan_url", "error_code", "error_message")}


@app.get("/api/examples/{example_id}")
def get_example(example_id: str):
    case, raw = load_example(example_id)
    return review(case, raw)


@app.post("/api/examples/{example_id}/register")
def register_example(example_id: str):
    _, raw = load_example(example_id)
    return register(raw)


@app.post("/api/review")
async def post_review(request: Request):
    raw = await request.body()
    try:
        case = json.loads(raw)
    except json.JSONDecodeError as e:
        raise HTTPException(400, f"JSON을 읽을 수 없음: {e}")
    return review(case, raw)


@app.post("/api/register")
async def register_upload(request: Request):
    return await run_in_threadpool(register, await request.body())
