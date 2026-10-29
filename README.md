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
