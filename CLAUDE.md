# CLAUDE.md

AI 모델 계보 기반 저작권 사용료 자동 정산 시스템 (블록체인특성화대학 PBL, 협력기업 엘에스웨어).
평가 11/20, 경진대회 11/26, 기능 동결 11/14. 팀 3명 (A: AI 추출·정답표, B: 정책 엔진·web3.py, C: 컨트랙트·Streamlit·발표).

## 범위 (넘어서지 않음)

- HF 공개 모델 약 10개, Sepolia 테스트넷, 1단계 분배(모델 1개 → 권리자 목록 + 비율)
- 모델을 학습·실행하지 않음. 공개 문서에서 직접 연결된 재료(베이스 모델·데이터셋·어댑터)만 확인
- 하지 않음: ERC-20, EIP-712, FastAPI, 다단계 계보 정산, NFT 발행
- 기존 폴더 구조(`app`, `backend`, `contracts`, `docs`, `fixtures`)를 따름. 새 최상위 폴더를 임의로 만들지 않음

## 흐름

수집(HF 카드 + 파일 SHA-256) → LLM 추출(LLM을 쓰는 유일한 단계) → 스키마·인용 검증 → 정책 엔진(상태 + 비율) → Reviewer 승인(Streamlit) → `registerSettlementRule` → Operator `deposit` → 권리자 `claim`

## 컨트랙트 (`contracts/`)

- `registerSettlementRule(modelHash, recipients[], bps[], escrowBps, evidenceHash)`: onlyReviewer, `sum(bps)+escrowBps == 10000`, 같은 해시 재등록 거부
- `deposit(modelHash)` payable: onlyOperator, 등록된 해시만. `claimable[modelHash][addr]`와 모델별 escrow에 기록. 반올림 잔여(dust) 처리
- `claim(modelHash)`: 호출자 본인 몫만. 잔액을 0으로 만든 뒤 전송, ReentrancyGuard
- `releaseEscrow(modelHash, recipient, amount, releaseEvidenceHash)`: onlyReviewer, 직접 송금이 아니라 수령자의 claimable로 이동
- OpenZeppelin AccessControl, 등록·입금·수령·escrow 해제 이벤트 emit
- 테스트: 무권한 호출 거부, 미등록 해시 거부, 재진입 방어

## 정책 엔진 (`backend/`)

- 모델 상태: `APPROVED` / `APPROVED_WITH_ESCROW` / `HOLD` / `REVIEW`. 입금 요청 결과 `ACCEPTED`/`REJECTED`는 별도(모델 상태 아님)
- 판정 순서: 재료 중 commercial_use=prohibited → HOLD / 불일치·불명·근거 부족 → REVIEW / 조건 확인됐으나 일부 수령자 미확인 → APPROVED_WITH_ESCROW / 모두 확인 → APPROVED
- commercial_use 분류: allowed(mit, apache-2.0, bsd-*), conditional(llama3.2, openrail 계열), prohibited(이름에 nc 포함, 비상업 명시), unknown. 정답표 규칙표와 항상 같게 유지
- 비율 산정: **미확정**. 기본 제안은 정책 엔진이 역할 가중치(예: base 50 / dataset 30 / adapter 20, 재료가 적으면 재정규화, 매핑 불가 몫은 escrowBps)로 계산하고 Reviewer가 승인. 팀 합의 전까지 확정 사항으로 쓰지 않음
- 발표 시 한계 명시: 비율의 객관적 근거 없음, Reviewer는 운영 측 담당자 역할(시연에서는 팀원이 수행)

## 스키마·정답표 (`docs/`, `fixtures/`)

- 스키마: `docs/schema-v0.2.json`. 예시: `fixtures/03_review_m4xx101.json`
- 열거형: role(base_model/dataset/adapter/merged_from), relationship(base_for/trained_on/adapter_of/merged_into), publisher_confidence(high/medium/low/unknown), rights_holder_status(unknown/not_verified/demo_confirmed; verified는 실제 본인 인증 시에만 사용, 데모에서는 쓰지 않음), commercial_use(allowed/conditional/prohibited/unknown), split(dev/eval)
- 정답표 규칙: 카드에 적힌 것만 쓰기, 모르면 `null`(빈 문자열·"unknown" 금지), 근거 문장은 원문 그대로, 근거 없는 rights_holder는 `null`, 이름은 소문자 원본 ID, 선언 라이선스와 상위 라이선스를 분리해 대조
- dev/eval 분리: eval 정답표는 프롬프트 수정 시 보지 않고 마지막에 한 번만 채점. 확정한 정답표는 이유 없이 고치지 않음
- 걸린 시간(`annotation_minutes`)을 기록 (사람 vs 시스템 비교 근거)

## AI 추출 원칙

- AI는 근거 추출 보조. 비율·지갑 주소·승인·법적 판단은 하지 않음
- 모든 항목에 원문 인용 필수, 원문에 없으면 폐기, 모르면 `null`
- 코드로 파싱 가능한 메타데이터(license, base_model, datasets)를 먼저 사용하고 LLM은 본문 해석에만 사용
- JSON Schema 검증, temperature 약 0, 프롬프트는 코드(`prompts/`)에 고정
- 평가: 권리자 정밀도·재현율(정밀도 우선), 라이선스 분류 정확도, 인용 검증 통과율, 정책 판정 일치율, "정답 HOLD인데 시스템이 승인" 오류 수

## 시연 4가지

1. 정상 승인 (APPROVED, 매핑된 권리자) 2. 비상업 베이스 파생 → HOLD, 입금 거부 3. 권리자 불명 → escrow 4. 변조된 파일(해시 불일치) → 미등록으로 거부

## 보안 규칙 (반드시 지킴)

- `.env`를 읽거나 출력하거나 커밋하지 않음. 개인키·시드 구문을 출력하거나 코드·문서에 쓰지 않음
- 키는 `.env`로만 관리. 필요한 변수: `SEPOLIA_RPC_URL`, `REVIEWER_PRIVATE_KEY`, `OPERATOR_PRIVATE_KEY`, `HOLDER_A/B/C_PRIVATE_KEY` (`.env.example`에는 값을 비워 둠)
- 테스트 지갑(Sepolia)만 사용. 실제 자산·기존 프로젝트 계정·Account 1 사용 금지. 메인넷 연결 금지
- 서버에 Reviewer 키를 두는 것은 시연용 단순화이며 한계로 명시
- 지갑 역할: Reviewer(배포·등록·해제), Operator(입금), Holder_A/B/C(claim). 시연 매핑: Meta→Holder_A, teknium→Holder_B, m4xx101→Holder_C

## 작업 규칙

- 확정되지 않은 결정을 임의로 구현하지 않고 미확정으로 표시해 팀에 확인
- commit/push는 사용자가 요청할 때만 수행
- 문서·주석은 한국어, 식별자는 영어
