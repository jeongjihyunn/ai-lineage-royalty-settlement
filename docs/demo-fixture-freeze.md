# 시연 fixture 동결 기준

온체인 등록 전에 아래 시연 fixture의 JSON 내용과 해시를 고정한다.

| Fixture | 목적 | 최종 상태 | 온체인 등록 |
|---|---|---|---|
| `01_normal_paper_qa.json` | 원본 어댑터 파일 해시로 정상 정산을 시연한다. | `APPROVED` | 등록 대상 |
| `02_hold_noncommercial.json` | 비상업 조건이 있는 베이스 파생 모델의 정산 보류를 시연한다. | `HOLD` | 등록·입금하지 않음 |
| `03_escrow_unknown_recipient.json` | 확인되지 않은 수령자 몫을 에스크로에 두는 정산을 시연한다. | `APPROVED_WITH_ESCROW` | 등록 대상 |
| `04_hash_mismatch.json` | 01의 원본 해시 규칙을 재사용하고, 원본 끝에 1바이트를 추가한 변조 파일의 해시로 `deposit`을 시도해 거부되는 것을 시연한다. | `APPROVED` | 별도 등록하지 않음 |

온체인 등록 대상은 01 정상 정산과 03 에스크로뿐이다. 02는 `HOLD`이므로 등록하거나 입금하지 않는다. 04의 `REJECTED`는 모델·정책 상태가 아니라 등록되지 않은 변조 해시로 `deposit`을 시도한 실행 결과다. 따라서 04는 01의 원본 해시 규칙만 사용하며 별도 정산 규칙을 등록하지 않는다.

`evidenceHash`는 fixture JSON 파일 전체의 SHA-256이다. JSON 내용이 바뀌면 `evidenceHash`도 바뀐다. 온체인 등록 후 fixture를 변경하면 기존 등록 내용과 달라지므로 변경한 JSON의 해시를 반영해 정산 규칙을 다시 등록해야 한다. 등록 전에 fixture JSON을 고정한다.

이 동결 단계에서는 새 컨트랙트 주소를 `.env`에 적용하지 않으며, 실제 체인 등록도 하지 않는다.
