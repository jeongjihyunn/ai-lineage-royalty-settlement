"""정답표(fixtures) 기준으로 추출 파이프라인 전체를 돌려 평가 수치를 낸다.

/api/extract와 같은 순서로 실행한다:
HF 메타데이터 초안 → README 조회 → LLM 추출(인용 검증 포함) → 스키마 검증 → 정책 엔진.

- dev 분할은 프롬프트를 고칠 때 여러 번 돌려도 된다.
- eval 분할은 마지막에 한 번만 채점한다(팀 평가 규칙). 실수로 돌리지 않도록 --final이 있어야 실행한다.
- HF에 없는 시연용 모델(조회 실패)은 시스템 오류가 아니므로 지표에서 빼고 목록에만 남긴다.
- LLM 추출·스키마 검증 실패는 시스템 결과로 보고 지표에 넣는다(상태 없음 = 판정 불일치).

실행(저장소 맨 위에서): python -m backend.run_evaluation --split dev
"""

from __future__ import annotations

import argparse
from collections.abc import Callable
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
import time
from typing import Any

from backend import evaluation_metrics as em
from backend import extraction_validator as ev
from backend import hf_metadata as hm
from backend import hf_readme as hr
from backend import llm_extractor as le
from backend import llm_provider as lp
from backend import policy_engine as pe

ROOT = Path(__file__).resolve().parents[1]
FIXTURES_DIR = ROOT / "fixtures"
RESULTS_DIR = ROOT / "docs" / "eval-results"

FETCH_FAILED = "FETCH_FAILED"
EXTRACTION_FAILED = "EXTRACTION_FAILED"
VALIDATION_FAILED = "VALIDATION_FAILED"
OK = "OK"


def load_fixtures(split: str, fixtures_dir: Path = FIXTURES_DIR) -> list[tuple[str, dict[str, Any]]]:
    """gold.split이 split과 같은 fixture를 (파일 이름, JSON) 목록으로 읽는다."""
    selected = []
    for path in sorted(fixtures_dir.glob("*.json")):
        fixture = json.loads(path.read_text(encoding="utf-8"))
        gold = fixture.get("gold") if isinstance(fixture.get("gold"), dict) else {}
        if gold.get("split") == split:
            selected.append((path.name, fixture))
    return selected


def run_case(
    fixture: dict[str, Any],
    call_llm: Callable[[str], str],
    build_case_draft: Callable[[str], dict[str, Any]] = hm.build_case_draft,
    fetch_readme: Callable[[str], str] = hr.fetch_readme,
    wallet_map: dict[str, str] | None = None,
) -> dict[str, Any]:
    """fixture 하나를 시스템에 넣고 결과를 돌려준다. 정답표(gold)는 시스템에 넘기지 않는다."""
    model_id = (fixture.get("input") or {}).get("model_id")
    started = time.perf_counter()
    result: dict[str, Any] = {
        "model_id": model_id,
        "outcome": None,
        "errors": [],
        "case": None,
        "policy": None,
        "source_text": None,
        "seconds": None,
    }

    try:
        case_draft = build_case_draft(model_id)
        readme = fetch_readme(model_id)
    except Exception as error:  # 네트워크·404 등
        result["outcome"] = FETCH_FAILED
        result["errors"] = [{"path": "fetch", "message": str(error) or type(error).__name__}]
        result["seconds"] = round(time.perf_counter() - started, 2)
        return result

    result["source_text"] = readme
    extraction = le.extract_case_from_readme(readme, case_draft, call_llm=call_llm)
    if not extraction["valid"]:
        result["outcome"] = EXTRACTION_FAILED
        result["errors"] = extraction["errors"]
    else:
        case = extraction["case"]
        validation = ev.validate_case(case)
        if not validation["valid"]:
            result["outcome"] = VALIDATION_FAILED
            result["errors"] = validation["errors"]
        else:
            policy = pe.evaluate(case, pe.load_wallet_map() if wallet_map is None else wallet_map)
            case["policy"]["automated_status"] = policy["status"]
            result["outcome"] = OK
            result["case"] = case
            result["policy"] = {"status": policy["status"], "reasons": policy["reasons"]}

    result["seconds"] = round(time.perf_counter() - started, 2)
    return result


def _gold_status(fixture: dict[str, Any]) -> str | None:
    gold = fixture.get("gold")
    return gold.get("expected_status") if isinstance(gold, dict) else None


def summarize(runs: list[tuple[str, dict[str, Any], dict[str, Any]]]) -> dict[str, Any]:
    """(파일 이름, fixture, run_case 결과) 목록을 지표로 묶는다. 조회 실패 사례는 지표에서 뺀다."""
    scored = [(name, fixture, run) for name, fixture, run in runs if run["outcome"] != FETCH_FAILED]
    metric_inputs = [
        {
            "gold": fixture,
            "prediction": {"case": run["case"] or {}, "policy": run["policy"] or {}},
            "source_text": run["source_text"],
        }
        for _, fixture, run in scored
    ]
    metrics = em.evaluate_cases(metric_inputs)

    hold_but_approved = sum(
        1
        for _, fixture, run in scored
        if _gold_status(fixture) == pe.HOLD
        and (run["policy"] or {}).get("status") in (pe.APPROVED, pe.APPROVED_WITH_ESCROW)
    )
    human_minutes = [
        fixture["gold"].get("annotation_minutes")
        for _, fixture, _ in scored
        if isinstance(fixture["gold"].get("annotation_minutes"), (int, float))
    ]
    system_seconds = [run["seconds"] for _, _, run in scored if run["outcome"] == OK]

    cases = []
    for (name, fixture, run), detail in zip(scored, metrics["cases"]):
        cases.append(
            {
                "file": name,
                "model_id": run["model_id"],
                "outcome": run["outcome"],
                "seconds": run["seconds"],
                "expected_status": detail["policy"]["expected"],
                "predicted_status": detail["policy"]["predicted"],
                "lineage": {k: detail["lineage"][k] for k in ("true_positive", "false_positive", "false_negative")},
                "declared_license": {
                    k: detail["licenses"]["declared_license"][k] for k in ("expected", "predicted", "correct")
                },
                "citations": {k: detail["citations"][k] for k in ("grounded", "denominator")},
                "errors": run["errors"],
            }
        )

    return {
        "metrics": {k: metrics[k] for k in metrics if k not in ("numerators", "denominators", "cases")},
        "numerators": metrics["numerators"],
        "denominators": metrics["denominators"],
        "hold_but_approved_count": hold_but_approved,
        "extraction_success": {
            "ok": sum(run["outcome"] == OK for _, _, run in scored),
            "total": len(scored),
        },
        "time": {
            "human_annotation_minutes_total": sum(human_minutes) if human_minutes else None,
            "human_annotated_cases": len(human_minutes),
            "system_seconds_total": round(sum(system_seconds), 2) if system_seconds else None,
            "system_seconds_mean": round(sum(system_seconds) / len(system_seconds), 2) if system_seconds else None,
        },
        "skipped_fetch_failed": [
            {"file": name, "model_id": run["model_id"], "errors": run["errors"]}
            for name, _, run in runs
            if run["outcome"] == FETCH_FAILED
        ],
        "cases": cases,
    }


def _pct(numerator: int, denominator: int) -> str:
    if not denominator:
        return "  -   (0/0)"
    return f"{numerator / denominator * 100:5.1f}% ({numerator}/{denominator})"


def format_report(summary: dict[str, Any], split: str) -> str:
    n, d = summary["numerators"], summary["denominators"]
    rows = [
        ("계보 정밀도", "lineage_precision"),
        ("계보 재현율", "lineage_recall"),
        ("선언 라이선스 정확도", "declared_license_accuracy"),
        ("상위 라이선스 정확도", "upstream_license_accuracy"),
        ("README 인용 검증 통과율", "citation_grounding_rate"),
        ("정책 판정 일치율", "policy_status_accuracy"),
    ]
    ok = summary["extraction_success"]
    lines = [f"[{split}] 평가 결과", ""]
    lines.append(f"  {'추출 성공':<22} {_pct(ok['ok'], ok['total'])}")
    for label, key in rows:
        lines.append(f"  {label:<22} {_pct(n[key], d[key])}")
    lines.append(f"  {'위험 승인(정답 HOLD/REVIEW→승인)':<22} {n['unsafe_approval_count']}건 / {d['unsafe_approval_count']}건")
    lines.append(f"  {'정답 HOLD인데 승인':<22} {summary['hold_but_approved_count']}건")

    t = summary["time"]
    human = (
        f"{t['human_annotation_minutes_total']}분 ({t['human_annotated_cases']}건 기록)"
        if t["human_annotation_minutes_total"] is not None
        else "기록 없음 (gold.annotation_minutes 비어 있음)"
    )
    system = f"평균 {t['system_seconds_mean']}초" if t["system_seconds_mean"] is not None else "-"
    lines += ["", f"  사람 정답표 작성 시간: {human}", f"  시스템 처리 시간: {system}", "", "사례별:"]
    for c in summary["cases"]:
        lin = c["lineage"]
        mark = "O" if c["expected_status"] == c["predicted_status"] else "X"
        lines.append(
            f"  {mark} {c['file']:<34} 정답 {str(c['expected_status']):<20} 시스템 {str(c['predicted_status']):<20}"
            f" 계보 TP{lin['true_positive']} FP{lin['false_positive']} FN{lin['false_negative']}"
            f"  {c['outcome']} {c['seconds']}s"
        )
        for err in c["errors"][:3]:
            lines.append(f"      - {err.get('path')}: {err.get('message')}")
    if summary["skipped_fetch_failed"]:
        lines += ["", "지표에서 제외(HF 조회 실패, 시연용 가상 모델 등):"]
        for s in summary["skipped_fetch_failed"]:
            lines.append(f"  - {s['file']} ({s['model_id']})")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="정답표 기준으로 추출·정책 파이프라인을 평가한다.")
    parser.add_argument("--split", choices=("dev", "eval"), default="dev")
    parser.add_argument("--final", action="store_true", help="eval 분할 최종 채점(한 번만)")
    parser.add_argument("--no-save", action="store_true", help="결과 JSON을 docs/eval-results에 저장하지 않음")
    args = parser.parse_args(argv)

    if args.split == "eval" and not args.final:
        print("eval 분할은 마지막에 한 번만 채점합니다. 정말 최종 채점이면 --final을 붙이세요.", file=sys.stderr)
        return 2

    try:
        from dotenv import load_dotenv

        load_dotenv(ROOT / ".env", override=False)
    except ImportError:
        pass

    try:
        call_llm = lp.build_call_llm_from_env()
    except (ValueError, RuntimeError) as error:
        print(f"LLM 설정 오류: {error}", file=sys.stderr)
        return 2

    fixtures = load_fixtures(args.split)
    if not fixtures:
        print(f"{args.split} 분할 fixture가 없습니다.", file=sys.stderr)
        return 2

    runs = []
    for name, fixture in fixtures:
        print(f"... {name}", file=sys.stderr)
        runs.append((name, fixture, run_case(fixture, call_llm)))

    summary = summarize(runs)
    print(format_report(summary, args.split))

    if not args.no_save:
        RESULTS_DIR.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        out = RESULTS_DIR / f"{args.split}-{stamp}.json"
        record = {
            "split": args.split,
            "run_at": stamp,
            "llm_provider": os.environ.get("LLM_PROVIDER"),
            "llm_model": os.environ.get("LLM_MODEL"),
            **summary,
            "predictions": {name: run["case"] for name, _, run in runs},
        }
        out.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n저장: {out.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
