"""Reviewer 화면: case JSON을 정책 엔진으로 판정하고 계보·분배·근거를 보여 준 뒤 등록을 승인한다.

- 정책 판정은 backend/policy_engine.py의 evaluate를 그대로 쓴다.
- 등록(registerSettlementRule) 호출은 B의 web3 함수가 나오면 연결한다. 지금은 등록 인자만 미리 보여 준다.

실행: streamlit run app/app.py
"""

import hashlib
import json
import sys
from html import escape
from pathlib import Path

import streamlit as st

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

import policy_engine as pe  # noqa: E402

FIXTURES_DIR = ROOT / "fixtures"

STATUS_VIEW = {
    pe.APPROVED: ("정산 가능", "approved", "모든 재료의 라이선스와 수령 지갑이 확인됐습니다."),
    pe.APPROVED_WITH_ESCROW: ("일부 보관 후 정산", "escrow", "지갑이 확인되지 않은 몫은 컨트랙트에 보관하고 나머지를 나눕니다."),
    pe.HOLD: ("정산 보류", "hold", "상업적 사용이 막힌 재료가 있어 사용료를 나눌 수 없습니다."),
    pe.REVIEW: ("사람 검토 필요", "review", "근거가 부족하거나 조건이 붙어 있어 담당자가 직접 확인해야 합니다."),
}
ROLE_VIEW = {
    "base_model": ("베이스 모델", "#6366f1"),
    "dataset": ("데이터셋", "#0ea5e9"),
    "adapter": ("어댑터", "#f59e0b"),
}
EXAMPLES = {
    "01_normal_paper_qa": "정상 정산",
    "02_hold_noncommercial": "상업 금지 재료",
    "03_escrow_unknown_recipient": "지갑 미확인",
    "03_review_m4xx101": "라이선스 애매",
    "04_hash_mismatch": "파일 바꿔치기",
}
STEPS = ["모델 카드 분석", "정책 판정", "Reviewer 승인", "블록체인 등록"]
APPROVABLE = (pe.APPROVED, pe.APPROVED_WITH_ESCROW)

CSS = """
<style>
.block-container {max-width: 900px; padding-top: 2.5rem;}
.stAppDeployButton {display: none;}
.hero h1 {font-size: 1.9rem; margin: 0 0 .25rem; padding: 0;}
.hero {margin-bottom: 1.5rem;}
.hero p {opacity: .65; margin: 0;}
.steps {display: flex; gap: .5rem; margin: 1.25rem 0 1.75rem;}
.step {flex: 1; padding: .55rem .7rem; border-radius: 10px; border: 1px solid rgba(128,128,128,.25); font-size: .85rem; opacity: .45; word-break: keep-all;}
@media (max-width: 640px) {.steps {gap: .3rem;} .step {padding: .45rem .5rem; font-size: .75rem;} .step small {display: none;}}
.step small {display: block; font-size: .7rem; font-weight: 600; opacity: .8;}
.step.done {opacity: 1;}
.step.done small::after {content: " ✓";}
.step.now {opacity: 1; border-color: #6366f1; box-shadow: inset 0 0 0 1px #6366f1;}
.step.blocked {opacity: 1; border-color: rgba(239,68,68,.6); color: #ef4444;}
.verdict {border-radius: 14px; padding: 1.1rem 1.3rem; margin-bottom: 2rem; border: 1px solid;}
.verdict.approved {background: rgba(34,197,94,.09); border-color: rgba(34,197,94,.45);}
.verdict.escrow {background: rgba(59,130,246,.09); border-color: rgba(59,130,246,.45);}
.verdict.hold {background: rgba(239,68,68,.08); border-color: rgba(239,68,68,.45);}
.verdict.review {background: rgba(245,158,11,.10); border-color: rgba(245,158,11,.5);}
.verdict .model {font-size: .8rem; opacity: .65; word-break: break-all;}
.verdict .label {font-size: 1.45rem; font-weight: 700; margin: .15rem 0;}
.lineage {display: flex; gap: .9rem; flex-wrap: wrap; align-items: center; margin-bottom: 2rem;}
.parts {display: flex; flex-direction: column; gap: .5rem; flex: 1 1 340px;}
.part {border-left: 4px solid; border-radius: 8px; padding: .5rem .8rem; background: rgba(128,128,128,.07);}
.part .role {font-size: .72rem; font-weight: 600;}
.part .name {font-weight: 600; word-break: break-all;}
.part .meta {font-size: .78rem; opacity: .75; margin-top: .1rem;}
.tag {display: inline-block; padding: 0 .4rem; border-radius: 4px; background: rgba(128,128,128,.15); font-family: monospace; font-size: .72rem;}
.tag.warn {background: rgba(239,68,68,.15); color: #ef4444;}
.arrow {font-size: 1.5rem; opacity: .4;}
.target {flex: 0 1 230px; border-radius: 12px; padding: 1rem; border: 1px dashed rgba(128,128,128,.55); text-align: center; word-break: break-all;}
.target small {display: block; opacity: .6; font-size: .72rem;}
.bar {display: flex; height: 34px; border-radius: 8px; overflow: hidden; margin: .4rem 0 .8rem;}
.bar div {display: flex; align-items: center; justify-content: center; color: #fff; font-size: .8rem; font-weight: 600;}
.bar .escrow {background: repeating-linear-gradient(45deg, #94a3b8, #94a3b8 6px, #a3aec0 6px, #a3aec0 12px);}
.legend {display: grid; grid-template-columns: auto 1fr auto auto; gap: .35rem .9rem; align-items: center; font-size: .85rem; margin-bottom: 2rem;}
.legend .dot {width: 10px; height: 10px; border-radius: 3px;}
.legend .wallet {font-family: monospace; font-size: .78rem; opacity: .7;}
.legend .pct {font-weight: 700; text-align: right;}
.reason {border-radius: 10px; padding: .7rem .9rem; background: rgba(128,128,128,.07); margin-bottom: .6rem;}
.reason .target-name {font-size: .75rem; opacity: .65;}
.quote {border-left: 3px solid rgba(128,128,128,.4); padding: .1rem 0 .1rem .7rem; margin-top: .45rem; font-size: .85rem; opacity: .85;}
.quote small {display: block; opacity: .6; font-size: .72rem; word-break: break-all;}
h3 {margin-top: .5rem;}
</style>
"""


def short_wallet(wallet):
    return f"{wallet[:6]}…{wallet[-4:]}" if wallet and len(wallet) > 12 else (wallet or "")


def load_case():
    """모델 정보 JSON을 올리거나 팀 예제를 고르면 (case, 원본 바이트, 이름)을 반환한다."""
    uploaded = st.file_uploader("모델 정보 파일 (JSON)", type="json")
    if uploaded:
        raw, name = uploaded.getvalue(), uploaded.name
    else:
        fixtures = {p.stem: p for p in sorted(FIXTURES_DIR.glob("*.json"))}
        stem = st.pills(
            "또는 팀이 만든 예제",
            list(fixtures),
            format_func=lambda s: EXAMPLES.get(s, s),
            default=next(iter(fixtures), None),
        )
        if not stem:
            return None
        raw, name = fixtures[stem].read_bytes(), fixtures[stem].name
    try:
        return json.loads(raw), raw, name
    except json.JSONDecodeError as e:
        st.error(f"JSON을 읽을 수 없음: {e}")
        return None


def steps_html(states):
    cells = "".join(
        f'<div class="step {state}"><small>STEP {i + 1}</small>{title}</div>'
        for i, (title, state) in enumerate(zip(STEPS, states))
    )
    return f'<div class="steps">{cells}</div>'


def show_verdict(result, model_id):
    label, cls, desc = STATUS_VIEW[result["status"]]
    st.markdown(
        f'<div class="verdict {cls}"><div class="model">{escape(model_id)}</div>'
        f'<div class="label">{label}</div>'
        f"<div>{desc}</div></div>",
        unsafe_allow_html=True,
    )


def show_lineage(case, result, model_id):
    st.subheader("이 모델을 만든 재료")
    wallets = {(r["role"], r["name"]): r["wallet"] for r in result["recipients"]}
    approvable = result["status"] in APPROVABLE
    parts = []
    for comp in case.get("components") or []:
        role, color = ROLE_VIEW.get(comp.get("role"), (comp.get("role"), "#94a3b8"))
        meta = f'<span class="tag">{escape(comp.get("license_id") or "license ?")}</span>'
        if approvable:
            wallet = wallets.get((comp.get("role"), comp.get("name")))
            meta += f" &nbsp;{short_wallet(wallet)}" if wallet else ' &nbsp;<span class="tag warn">지갑 미확인</span>'
        parts.append(
            f'<div class="part" style="border-color:{color}">'
            f'<div class="role" style="color:{color}">{role}</div>'
            f'<div class="name">{escape(comp.get("name") or "")}</div>'
            f'<div class="meta">{meta}</div></div>'
        )
    st.markdown(
        f'<div class="lineage"><div class="parts">{"".join(parts)}</div>'
        f'<div class="arrow">→</div>'
        f'<div class="target"><small>검토 대상 모델</small><b>{escape(model_id)}</b></div></div>',
        unsafe_allow_html=True,
    )


def show_shares(result):
    st.subheader("사용료 분배")
    segments, rows = [], []
    for r in result["recipients"]:
        role, color = ROLE_VIEW.get(r["role"], (r["role"], "#94a3b8"))
        pct = r["share_bps"] / 100
        segments.append(f'<div style="width:{pct}%;background:{color}">{pct:g}%</div>')
        rows.append(
            f'<div class="dot" style="background:{color}"></div><div>{role} · {escape(r["name"])}</div>'
            f'<div class="wallet" title="{r["wallet"]}">{short_wallet(r["wallet"])}</div><div class="pct">{pct:g}%</div>'
        )
    if result["escrow_bps"]:
        pct = result["escrow_bps"] / 100
        segments.append(f'<div class="escrow" style="width:{pct}%">{pct:g}%</div>')
        rows.append(
            '<div class="dot" style="background:#94a3b8"></div><div>에스크로 · 컨트랙트에 보관</div>'
            f'<div class="wallet">—</div><div class="pct">{pct:g}%</div>'
        )
    st.markdown(
        f'<div class="bar">{"".join(segments)}</div><div class="legend">{"".join(rows)}</div>',
        unsafe_allow_html=True,
    )


def show_register(result, case, raw):
    """승인 버튼을 그리고 단계 표시 상태(STEP 3, 4)를 반환한다."""
    st.subheader("Reviewer 승인")
    if result["status"] not in APPROVABLE:
        st.caption("이 상태에서는 정산 규칙을 등록할 수 없습니다.")
        return "blocked", ""

    model_hash = pe._as_dict(case.get("input")).get("artifact_sha256")
    if not model_hash:
        st.warning("모델 파일 해시(input.artifact_sha256)가 없어 등록할 수 없습니다.")
        return "blocked", ""
    try:
        pe.to_contract_args(result, model_hash, hashlib.sha256(raw).hexdigest())
    except ValueError as e:
        st.error(f"등록 인자 생성 실패: {e}")
        return "blocked", ""

    if not st.button("승인하고 블록체인에 등록", type="primary"):
        return "now", ""
    st.warning("블록체인 등록 함수 연결 전입니다.")
    return "done", "now"


def show_reasons(reasons):
    st.subheader("판정 근거")
    for reason in reasons:
        target = f'<div class="target-name">{escape(reason["target"])}</div>' if reason.get("target") else ""
        quotes = ""
        for ev in reason.get("evidence") or []:
            where = " · ".join(escape(str(v)) for v in (ev.get("evidence_location"), ev.get("source_url")) if v)
            quotes += f'<div class="quote">“{escape(ev["evidence_quote"])}”<small>{where}</small></div>'
        st.markdown(
            f'<div class="reason">{target}<div>{escape(reason["message"])}</div>{quotes}</div>',
            unsafe_allow_html=True,
        )


def main():
    st.set_page_config(page_title="계보 정산 검토", page_icon="⛓️")
    st.markdown(CSS, unsafe_allow_html=True)
    st.markdown(
        '<div class="hero"><h1>계보 정산 검토</h1>'
        "<p>AI 모델을 만든 재료의 권리자에게 사용료를 나눠도 되는지 판정하고, 승인하면 분배 규칙을 블록체인에 등록합니다.</p></div>",
        unsafe_allow_html=True,
    )
    loaded = load_case()
    steps = st.empty()
    if not loaded:
        steps.markdown(steps_html(["", "", "", ""]), unsafe_allow_html=True)
        return
    case, raw, name = loaded
    model_id = pe._as_dict(case.get("input")).get("model_id") or name

    result = pe.evaluate(case, pe.load_wallet_map())
    show_verdict(result, model_id)
    show_lineage(case, result, model_id)
    if result["status"] in APPROVABLE:
        show_shares(result)
    approve_state, register_state = show_register(result, case, raw)
    if result["status"] != pe.APPROVED:
        show_reasons(result["reasons"])

    steps.markdown(steps_html(["done", "done", approve_state, register_state]), unsafe_allow_html=True)


main()
