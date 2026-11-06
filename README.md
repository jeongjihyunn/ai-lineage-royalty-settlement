# AI Model Lineage Royalty Settlement

Hugging Face 공개 모델 문서에서 모델 계보·라이선스·근거를 추출하고,
사람의 검토를 거쳐 테스트 사용료를 자동 정산하는 시연 프로젝트입니다.

## 핵심 흐름

Hugging Face 모델 문서
→ AI 근거 추출
→ 정책 판정
→ Reviewer 승인
→ Sepolia 스마트 컨트랙트 정산
→ 권리자 claim

## 범위

- 모델을 직접 학습하거나 실행하지 않음
- 공개 문서에서 직접 연결된 재료(베이스 모델·데이터셋·어댑터)만 확인
- AI는 법적 판단자가 아니라 근거 추출 보조 역할
- 최종 정산 비율·지갑 주소·승인은 사람이 결정
- Sepolia 테스트 ETH만 사용

## 실행

터미널 두 개가 필요합니다.

API 서버 (저장소 맨 위 폴더에서):

```bash
python3 -m venv .venv
.venv/bin/pip install -r app/api/requirements.txt
.venv/bin/uvicorn app.api.server:app --port 8000
```

화면:

```bash
cd app/web
npm install
npm run dev
```

브라우저에서 http://localhost:5173 을 엽니다. 처음 한 번만 설치하면 되고, 다음부터는 `uvicorn`과 `npm run dev`만 실행합니다.

## 배포 정보 (Sepolia)

| 항목 | 값 |
| --- | --- |
| 컨트랙트 | `LineageRoyaltySettlement` (`contracts/LineageRoyaltySettlement.sol`) |
| 컨트랙트 주소 | [`0x72b9C2BA2D0CC7c3f056EEA8E5B27A6E0069a432`](https://sepolia.etherscan.io/address/0x72b9C2BA2D0CC7c3f056EEA8E5B27A6E0069a432) |
| Reviewer | `0x9860b505569783A3f9b36a9cB1A3835745efE8f4` |
| Operator | `0x40F57730A509fE02c451cABE1A916258c5dA2661` |
| 배포 트랜잭션 | [`0x6f1c9bb0...b0c5f`](https://sepolia.etherscan.io/tx/0x6f1c9bb0c285e7bed5a26e99340386e6b9817a615ed88112fb2dcab92b0b0c5f) |

- 주소는 모두 공개 주소이며 개인키는 `.env`에만 두고 저장소에 올리지 않음
- Reviewer와 Operator는 생성자에서 고정되며 같은 지갑일 수 없음
- 컨트랙트를 다시 배포하면 주소가 바뀌므로 이 표를 함께 갱신함
