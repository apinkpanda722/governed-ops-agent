# M6 limits-delete live 결과 (2026-10-13)

사전 등록: `hypotheses-m6-finalize.md`(마무리 호출), `hypotheses-m6-api-authz.md`(삭제 권한 프로브). 이 문서는 결과 수치만 적는다(대화 본문·토큰·키 없음). 목표 수치가 없는 관찰이며 성공률·효과 주장은 하지 않는다.

## 1. 구현·검증

- PR #126(`m6-limits-delete` phase 완료, 병합 커밋 `dd9e231`, phase 완료 커밋 `5d2f062`). step 커밋: step0 `f8a5cbb`, step1 `7eaa71e`, step2 `3ed4211`, step3 `c1ff387`, step4 `eeff2c7`, step5 `21a85ab`, step6 `025ff0c`, step7 `2ae46a9`, step8 `4a86205`.
- `make verify`: pytest 3647 passed·7 skipped, vitest 211 passed, ruff·tsc 통과, eslint 경고 1, `playwright test --list` 3 spec.

## 2. 사용자 live 결과

### test-m5-pg
처음 시도는 Postgres 컨테이너가 꺼져 있어 6건 모두 실패했다(환경 문제, 코드 아님). 컨테이너 기동·`db-init` 뒤 재실행해 통과했다.

### 브라우저 관찰 (`hypotheses-m6-finalize.md` §6 형식)
각 회 새 대화, 같은 질문("INC-2026-012 장애보고에서 원인과 조치 내용을 목록으로 정리해줘").

| 사용자 | 회수 | 결과 |
|---|---|---|
| platform | 5 | 5회 모두 정상 완료(도구 2단계) |
| finance | 5 | 4회 정상 완료("찾을 수 없음", 도구 1단계), 1회 `max_steps_finalized`(도구 8단계·실패 1, 근거 있음, 화면에 한도 안내 문구, 답변은 확인하지 못한 내용을 확인하지 못했다고 밝힘) |

10턴 합계: `failed/max_steps` 0, `max_steps_finalized` 1, 그 밖의 완료 9, 그 밖의 실패 0. n=1이므로 성공률·효과 주장은 없다.

실행 편차: finance 첫 시도는 새 대화 5회가 아니라 한 대화 안의 이어 말하기 연속 턴이었다. 사전 등록(각 회 새 대화)에 맞춰 새 대화로 다시 5회 했고 위 집계는 재실행분이다. 첫 시도는 집계에서 제외한다.

### 삭제 시나리오 (전부 통과)
- 유휴 삭제: 목록에서 제거, 주소를 직접 열면 "대화를 찾을 수 없습니다".
- 승인 대기 중 삭제: 거부, 안내 문구 표시.
- 승인·발송 완료 뒤 삭제: 목록·승인함에서 제거, 직접 열면 찾을 수 없음.
- 확인창 문구 일치.

### 감사·보존
- `make audit-verify`: chains_pass=61, chains_fail=0, anchors=61, legacy_unverified 40파일(기존).
- `make smoke-audit`: RESULT PASS(변조 탐지 5종, scene5_tail_cut, 실제 체인 records=226).
- `make checkpoint-prune` dry-run: scanned=270 candidates=15 active=0 deleted=0 apply=false. 삭제 표시 대화의 DB 조회는 5개(오늘 만든 대화라 나이 기준으로는 후보가 될 수 없고, 나머지 10개는 나이 기준의 옛 thread로 해석한다. 대화별 분해는 하지 않았다). APPLY는 하지 않았다.

### e2e-web
PASS: 3개 스펙 통과(approval-flow, isolation, delete-conversation), 삭제 스펙은 로케이터 수정(PR #128의 fix 커밋) 뒤 통과. 수정 전 첫 실행에서는 `getByRole("alert")`가 Next의 `__next-route-announcer__`(role="alert")와 겹쳐 strict mode violation이 났다.

## 3. 비용 원장과 모델 호출 수

`data/manifest/costs.jsonl`의 이번 live 증분(2026-10-13 12:37~12:57 UTC = KST 21:37~21:57, 192줄 = 예약 96 + 정산 96)을 집계했다.

- 모델 호출 수: task 17개. 호출 수 분포는 2회 9개, 3회 7개, 9회 1개다. **9회인 task가 1개 있다**(8단계 뒤 마무리 호출 9번째로 해석하며, `max_steps_finalized` 관찰 1건과 개수가 일치한다. task와 화면 관찰의 대응은 task ID로 확인하지 않았다).
- 호출마다 `agent_primary` 정산과 `agent_fallback` 정산(0원, 토큰 0)이 한 쌍으로 남는다. fallback이 실제로 쓰인 호출은 0건이다.
- 이번 live의 실제 추가 비용: openai `agent_primary` $0.00608, anthropic $0.00000, 합계 **$0.00608**(예상 약 $0.02보다 낮다).

## 4. 알려진 한계

- 한도(마무리 호출) 경로는 live에서 n=1 관찰이다.
- 삭제는 소프트 삭제이며 원문은 다음 정리(7일 보존 정리) 때까지 남는다.
