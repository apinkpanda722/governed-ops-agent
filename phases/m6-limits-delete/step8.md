# Step 8: docs-sync

## 읽어야 할 파일

먼저 아래 파일들을 읽고 프로젝트의 아키텍처와 설계 의도를 파악하라:

- `/AGENTS.md`(계층 경계, CRITICAL 규칙)
- `/docs/ARCHITECTURE.md`, `/docs/ADR.md`, `/docs/THREAT_MODEL.md`
- `/docs/M6_DESIGN_DRAFT.md`(이 phase의 승인된 설계), `/docs/results/hypotheses-m6-finalize.md`, `/docs/results/hypotheses-m6-api-authz.md`(사전 등록, 읽기만 한다)
- `/.claude/skills/failure-patterns/SKILL.md` — 읽기만 한다
- `/docs/ARCHITECTURE.md`(API 표, 감사 이벤트, 보존 정책, 알려진 한계), `/docs/ADR.md`(ADR-024 형식과 변경 기록), `/docs/THREAT_MODEL.md`(위협 표, 완화 ↔ 검증 매핑, 잔여 위험), `/docs/PRD.md`(마일스톤 표와 그 아래 문단)
- `/tests/test_docs_consistency.py`(문서 계약 테스트: `test_m5_ui_decision_contract`, `test_m4_decisions_have_no_result_values` 등)
- 이전 step 산출물 전부(`/src/govops/api/`, `/src/govops/agent/graph.py`, `/web/src/components/conversation-list.tsx`)
- `/docs/results/hypotheses-m6-finalize.md`, `/docs/results/hypotheses-m6-api-authz.md`(읽기만 한다)

이전 step에서 만들어진 코드를 꼼꼼히 읽고, 설계 의도를 이해한 뒤 작업하라.

## 작업

**수정할 파일**(이 목록이 전부다): `docs/ARCHITECTURE.md`, `docs/ADR.md`, `docs/THREAT_MODEL.md`, `docs/PRD.md`, `tests/test_docs_consistency.py`. TDD.

열람 금지 경로: `data/interim/`
수정 허용 경로: `docs/ARCHITECTURE.md`, `docs/ADR.md`, `docs/THREAT_MODEL.md`, `docs/PRD.md`, `tests/test_docs_consistency.py`

열람 금지의 범위: 이 금지는 **Codex 세션이 `data/interim/` 아래 파일을 직접 열람·출력·인용하는 것**에 대한 것이다. 이 step은 문서만 다루고 `data/interim/`을 읽지 않는다.

**배경**: 구현된 동작을 문서에 반영한다. 코드·`docs/results/`는 바꾸지 않는다. 문서에는 결과 수치를 쓰지 않는다(`test_m4_decisions_have_no_result_values`).

### 1. `docs/ADR.md`

기존 ADR을 고치지 않고 **새 ADR-025**를 더한다(제목 예: "M6 결정 — 단계 상한 마무리 호출과 대화 소프트 삭제", 상태·날짜 2026-10-13, 결정·이유·트레이드오프·변경 기록 형식은 ADR-024를 따른다). 결정 항목:
1. 단계 상한 도달 시 마지막 호출 1회의 조건 순서(`turn_read_ok >= 1`, 비용 상한이면 호출만 생략하고 `failed/max_steps`), 마지막 응답의 도구 호출은 실행하지 않음, `stop_reason="max_steps_finalized"`, 시스템 프롬프트·도구 불변이라 골든·dev 불변.
2. 턴 범위 계수 `turn_read_ok`(대화 범위 `evidence_ids`는 쓰지 않음)와 N 재계산에서 `max_steps_finalized` 제외.
3. 소프트 삭제: 대화의 모든 턴 행에 `deleted_at`, `conversation_of`는 삭제된 대화에도 대화 ID를 돌려주고 서비스가 `is_deleted`로 판정한다(첫 턴 ID = 대화 ID).
4. 오류 코드: 404 `conversation_not_found`(타인·없음·삭제됨·형식 오류 동일), 실행 중 409 `turn_in_progress`, 승인 대기 409 `approval_pending`은 기존 코드를 재사용한다. 새 코드는 내부 실패용 500 `audit_write_failed`(감사 쓰기 실패·싱크 없음)·`delete_failed`(표시 실패) 두 개뿐이며 BFF가 502 `upstream_unavailable`로 접는다. 승인 권한 규칙(`approver_decision`)은 바꾸지 않는다.
5. 감사 `conversation.deleted`는 필드 없는 이벤트(`task_id`=대화 ID, `actor_sub`=요청자). 감사를 먼저 쓰고 표시가 실패하면 500이며 재시도하면 이벤트가 두 번 남을 수 있다.
6. 보존: 삭제 표시된 대화는 나이와 관계없이 다음 `checkpoint-prune` 후보(활성 상태는 제외). 원문은 다음 정리 때 제거되고 감사 기록과 이미 발송된 효과는 남는다. 하드 삭제는 이번 범위가 아니다.
7. 사이드바 승인 배지가 인박스 처리 직후 최대 10초 늦게 갱신되는 한계(공유 폴링 훅은 이번 범위에서 제외).

### 2. `docs/ARCHITECTURE.md`

API 표에 `DELETE /conversations/{conversation_id}`(200, 본문 `{conversation_id, deleted}`)를 더한다. 감사 이벤트 목록에 `conversation.deleted`를, 보존 정책 절에 삭제 표시 대화의 정리 규칙을 더한다. 알려진 한계 두 가지를 적는다: ① 삭제 표시 뒤 원문은 다음 정리까지 보존된다(마지막 활동 후 7일이 아니라 다음 `checkpoint-prune` 실행 때). ② 감사 기록 후 표시가 실패해 재시도하면 같은 삭제의 감사 이벤트가 두 번 남을 수 있다. ③ 사이드바 승인 배지의 최대 10초 지연. 확인창 문구를 정확히 적는다: "원문은 다음 정리 때 제거되고, 감사 기록과 이미 발송된 효과는 남는다."

### 3. `docs/THREAT_MODEL.md`

삭제 엔드포인트의 권한 우회(타인 대화 삭제·존재 여부 탐지·삭제 후 접근)를 위협 표에 행으로 더하고, 완화 ↔ 검증 매핑에 `hypotheses-m6-api-authz.md`의 결정론 프로브와 테스트 파일(`tests/test_api_conversation_delete.py`, `tests/test_api_conversation_delete_route.py`, `web/src/app/api/bff.integration.test.ts`)을 연결한다. 잔여 위험에 "삭제는 소프트이며 원문이 다음 정리까지 남는다"를 더한다. 기존 행 번호·표 형식을 따르고 기존 행을 바꾸지 않는다.

### 4. `docs/PRD.md`

마일스톤 표의 기존 행은 고치지 않는다. 표 바로 아래(빈 줄 하나 뒤, `make verify` 문단 앞)에 다음 한 줄을 **그대로** 덧붙인다: "M6 선행: m6-limits-delete(M5 뒷정리 — 단계 상한 마무리, 대화 소프트 삭제). 그래프 변경이라 공식 test(정상 대조 test 40건) 전에 머지한다." `tests/test_docs_consistency.py`의 기존 PRD 단언은 수정 없이 통과해야 한다.

### 5. `tests/test_docs_consistency.py`

문서 계약 테스트를 **더하기만** 한다(`test_m6_decision_contract`): ADR-025가 있고 위 7개 결정의 고정 표지(`max_steps_finalized`, `turn_read_ok`, `deleted_at`, `conversation.deleted`, 확인창 문구 문장)가 정확히 있으며 결과 수치(TCR·정확도 숫자)가 없고 ADR-001~024의 본문이 바뀌지 않았다. 기존 테스트의 단언은 약화하지 않는다. 새 테스트에 PRD 덧붙임 문장의 정확 일치 단언도 더한다.

**사용자 실행(이 step에서 실행하지 않는다)**: 없음.

## Acceptance Criteria

```bash
make verify   # ruff check + ruff format --check + pytest + web 검사
```

## 검증 절차

1. 위 AC 커맨드를 실행한다.
2. 아키텍처 체크리스트를 확인한다:
   - ARCHITECTURE.md 디렉토리 구조와 AGENTS.md 계층 경계를 따르는가?
   - ADR 기술 스택을 벗어나지 않았는가?
   - AGENTS.md CRITICAL 규칙을 위반하지 않았는가?
3. 결과를 최종 응답(JSON)으로 보고한다. `phases/` 아래 파일은 수정하지 않고 git commit도 하지 않는다:
   - 성공 → `"status": "completed"`, `"summary": "산출물 한 줄 요약"`
   - 해결할 수 없는 실패 → `"status": "error"`, `"reason": "구체적 에러 내용"`
   - 사용자 개입 필요 또는 지시가 서로 충돌해 판단할 수 없음 → `"status": "blocked"`, `"reason": "구체적 사유"` 후 즉시 중단

## 공통 금지사항

<!-- common-block:start -->
- **선점검**: 코드를 쓰기 전에 이 파일의 작업 지시·금지사항·"수정할 파일" 목록을 서로 대조한다. 충돌(하나가 요구한 것을 다른 것이 금지함, 수정할 파일 목록 밖 파일의 수정이 필요함, 계층 경계 위반)이 있으면 코드를 쓰지 않고 즉시 `blocked`로 보고한다. reason에는 충돌하는 두 문장을 그대로 인용한다. 충돌이 없으면 진행한다.
- **실제 서비스**: 실제 서비스(A MCP·LiteLLM·mock 컨테이너·외부 API)에 접속하는 코드는 작성할 수 있다. step 안에서 그 코드를 실행하거나 그 코드에 접속하는 테스트를 만들지 않는다. 테스트는 가짜 환경(가짜 실행기·로컬 루프백·합성 데이터)으로만 한다.
- **계층 경계**: `AGENTS.md`의 계층 경계 표와 `tests/test_architecture.py`를 따른다. 가드 테스트를 약화하지 않는다.
- **읽기 전용**: `.claude/`, `docs/results/`의 사전 등록 파일, `scripts/`, `phases/`, `data/contract/`, `data/eval/`, `data/replay/`·`data/results/`의 기록, A 저장소(`~/PycharmProjects/ko-docs-agentic-rag`)는 읽기만 한다(이 step의 "수정할 파일"에 있는 것만 예외). 기존 테스트의 단언을 약화하지 않는다.
- **비밀 값**: `.env`와 A의 `data/interim/mcp/tokens.json`을 열거나 출력하지 않는다. 토큰·키·Authorization 헤더를 코드·테스트·문서·로그·trace에 남기지 않는다.
<!-- common-block:end -->

## 이 step의 금지사항

- 기존 ADR(ADR-001~024)·`docs/results/` 파일을 수정하지 마라. 이유: 결정 이력과 사전 등록은 보존한다. 변경은 새 ADR-025로만 한다.
- 문서에 TCR·정확도·지연 **결과 수치**나 live 결과를 쓰지 마라. 설계 상수와 사전 등록된 건수(10초 지연, 7일 보존, 단계 상한 8, 정상 대조 test 40건)는 위 작업 지시대로 쓸 수 있다. 이유: 결정 문서에는 결과 수치가 없고(`test_m4_decisions_have_no_result_values`) 이번 phase는 live를 하지 않았다. `<!-- m4-current:start -->`·`<!-- m4-current:end -->` 블록을 새로 만들거나 옮기지 않는다(그 테스트가 블록 4개를 단언한다).
- `AGENTS.md`를 수정하거나 `docs/PRD.md`의 기존 마일스톤 표 행을 고치지 마라. 이유: CRITICAL 항목 수와 PRD 표를 단언하는 테스트가 있고 이 phase는 규칙을 바꾸지 않는다. PRD는 표 아래 한 줄 덧붙임뿐이다.
- `tests/test_docs_consistency.py`의 기존 단언을 고치거나 완화하지 마라. 이유: 새 테스트를 더하기만 한다(FP-061).
- "수정할 파일"에 없는 파일(`src/`·`web/`·`tests/test_docs_consistency.py` 밖의 다른 `tests/`·`docs/results/`·`AGENTS.md` 등)을 수정하지 마라. 이유: 이 step은 문서 네 개와 문서 계약 테스트만 맡는다.
