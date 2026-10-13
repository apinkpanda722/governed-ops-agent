# M6 설계 초안 (최종, 승인됨)

기준: main `d695f77`. 이 문서는 untracked이며 사전 등록 커밋(`feature/m6-prereg`)에 함께 넣는다(3절). 코드·phase 파일·사전 등록 파일은 아직 없다.

## 0. 외부 계약 실측

**실측 (근거 경로)**

| 사실 | 근거 |
|---|---|
| `MAX_STEPS=8`. `model_body` 맨 앞에서 `steps>=8`이면 `failed/max_steps`, 그 다음 `turn_cost_usd>=MAX_COST_USD`이면 `failed/max_cost`. 탐색 예산 소진은 `read_tools` 끝에서 먼저 판정 | `agent/graph.py:64,283-286,494` |
| 재생 키는 호출마다 `hash(messages, tools, max_output_tokens)`, 헤더 `prompt_hash`는 `system_prompt`·`tools`·`model`·`params`의 해시. 8단계 이후에만 도는 새 호출은 기존 기록의 키를 바꾸지 않는다 | `replay/llm.py:26,242,262` |
| `ChatClient.complete`에 `tool_choice` 인자 없음 | `gateway/client.py:122-130` |
| `conversation_id` = 첫 task ID = thread ID. 턴 인덱스 행 키는 `turn_id` | `api/tasks.py:91-96`, `api/conversations.py` |
| 대화 삭제 경로는 보존 정책의 하드 삭제(`delete_conversations`)뿐 | `api/retention.py:38` |
| 턴 인덱스는 `CREATE TABLE IF NOT EXISTS`만 사용. 기존 DB에 새 컬럼은 `ALTER TABLE … ADD COLUMN IF NOT EXISTS` 필요 | `api/conversations.py:107-125` |
| 시스템 이벤트가 아닌 감사 이벤트는 `task_id`(32 hex)·`actor_sub` 필수. 문자열 필드 값은 해시·mock ID·열거형만 통과. 필드 없는 이벤트는 일반 경로 통과 | `audit/log.py:106-130,238-281` |
| API 뷰는 `stop_reason`을 노출하지 않음(뷰·BFF 비노출 단언 존재). `error`는 노출 | `api/tasks.py:241-251`, `tests/test_api_conversation_views.py:82,118,272`, `web/src/ui-boundary.test.ts:12`, `web/src/lib/server/project.test.ts:26` |
| `useApprovals`는 사이드바(10초)와 승인함 화면(5초)에서 따로 폴링 | `web/src/components/sidebar.tsx:14`, `approval-inbox.tsx:13` |
| LiteLLM v1.103.3, Langfuse 3.225.11 | `infra/docker-compose.yml` |
| LiteLLM 훅은 `govops.guardrails`·`obs.mask`만 import 가능 | `tests/test_gateway_hook_imports.py` |
| v2 분류기·레드팀 live 러너 없음(생성기·검수 카드만). `costs.py`에는 `purpose=guardrail_v2`만 있고 Haiku 모델·단가 없음, LiteLLM 설정에도 Haiku 배포 없음 | `eval/redteam*.py`, `costs.py:22`, `infra/litellm/config.yaml` |
| 레드팀 120건은 `set_sha256`로 고정, 검수 확정본 존재. 세트·`allocation.json` 변경은 검수 무효 | `hypotheses-m6-redteam-addendum.md` §1, §4 |
| 정상 대조 60건 = 평가셋(dev 20 + test 40) 재사용, 오탐 정의에 "격리가 일어난 task" 포함 | `hypotheses-m4-redteam.md:11,104-107` |
| `smoke_guardrail`에 `langfuse_no_raw_input` 단계(obs 꺼짐 SKIP, trace 못 찾음 INCONCLUSIVE) | `gateway/smoke_guardrail.py:406` |

**미실측 (사용자 실행·확인 필요)**
- Haiku 계열 모델 ID·가격(`ARCHITECTURE.md:322` "확인 필요").
- Anthropic이 `tool_result` 이력과 함께 `tools` 없이 호출하는 요청을 거절하는지(초안은 `tools` 유지라 영향 없음, 단정하지 않음).
- 마지막 호출에서 모델이 도구 호출을 또 내는 빈도.
- v2 분류기의 응답 형태·지연·429 한도.
- Langfuse 공개 API가 차단된 요청의 trace를 남기는지.

## 1. 첫 phase 후보 판단

### 1-1. 단계 상한 도달 시 마지막 답변 1회 (a안)

**마지막 호출 조건 (위에서 아래로)**
1. `steps>=8`이 아니면 기존 동작.
2. `turn_cost_usd >= MAX_COST_USD`이면 **마지막 호출만 생략**하고 결과는 기존과 같은 `failed/max_steps`다. 판정 순서(max_steps가 max_cost보다 먼저)는 바꾸지 않는다.
3. **이번 턴에 성공한 읽기 결과가 1건 이상**이어야 한다. 없으면 `failed/max_steps`.
4. 모두 통과하면 마지막 호출 1회: 같은 `tools`에 고정 안내문 1개를 마지막 user 메시지로 덧붙인 `chat.complete`(시그니처 불변).
5. 응답에 도구 호출이 없고 본문이 있으면 `completed`, `stop_reason="max_steps_finalized"`, `cited_doc_ids`는 기존 방식. 도구 호출이 오거나 본문이 비면 `failed/max_steps`. **마지막 응답의 도구 호출은 실행도 초안 생성도 하지 않는다.**
6. `steps`는 8 유지. 마지막 호출도 `model.call` 감사, Guardrail 사전·사후 훅, 비용 원장을 기존 경로로 통과한다.

**"이번 턴 성공 읽기" 계수**
- `evidence_ids`는 대화 범위 누적이라 조건에 쓰지 않는다(앞 턴 근거로 마지막 호출이 열림). `observed_ids`는 턴 범위지만 ID 없는 성공 결과를 못 세고 오류 결과의 ID 여부가 미확인이라 쓰지 않는다.
- 새 턴 범위 상태 `turn_read_ok: int`. 계수 위치는 `read_tools_body`의 `success = not outcome.is_error`(`graph.py:464`). `AgentState`, `TURN_KEYS`, `build_turn_state` 초기값(`agent/turns.py`)에 추가.
- 소비자 테스트: `tests/test_agent_turns.py` 키 집합 정확 일치(91행 부근) — "~에 한함, 단언 약화 금지"로 허용 목록에 넣고 차이가 정확히 `{"turn_read_ok"}`가 되게 고정.

**테스트 (step 6)**
- 권한 실패만 있는 턴 → `failed/max_steps`, 마지막 호출 0회.
- 앞 턴에 근거가 있고(`evidence_ids` 있음) 이번 턴은 실패만(`turn_read_ok=0`) → `failed/max_steps`, 마지막 호출 0회.
- 이번 턴 성공 읽기 ≥1 → 마지막 호출 1회, `completed`, `stop_reason="max_steps_finalized"`.
- 마지막 응답이 도구 호출 → `failed/max_steps`, 도구 실행·초안 0건.
- 마지막 응답 본문 비어 있음 → `failed/max_steps`.
- 8단계 도달 + `turn_cost_usd >= MAX_COST_USD` → 마지막 호출 0회, `failed/max_steps`.
- 마지막 호출이 `model.call` 감사 1건, Guardrail 사전 훅 입력 통과(안내문이 `screen_request`를 통과하는 단위 테스트), 사후 훅 결과 처리, 비용 원장 예약·정산 1건씩을 만든다.
- 8단계 미만 상태에서 `chat.complete` 인자가 변경 전과 바이트 단위로 같다(골든 재생 테스트가 덮음).
- 기존 `tests/test_agent_limits.py` 단언(235행 `max_steps`·`steps == len(chat.calls) == MAX_STEPS`, 255행 `max_cost`)은 유지한다. 단 235행 테스트는 `ToolOutcome({}, False, ())`(성공 읽기) 8개를 써서 a안에서는 마지막 호출 경로로 들어가므로, **단언은 그대로 두고 설정의 도구 결과를 오류 결과로 바꿔** 8단계 도달 후 `turn_read_ok=0`이 되게 한다. 이 변경은 step 6 수정 허용 목록에 "설정 변경에 한함, 단언 약화 금지"로 명시한다. 대상 테스트는 `test_disabled_and_explicit_budget`이다. 원래 설정(성공 읽기 8개, `exploration_budget=None`)은 step 6의 **새 테스트**로 남겨 `completed`, `stop_reason="max_steps_finalized"`, `steps == 8`, 모델 호출 9회(`len(chat.calls) == 9`)를 단언한다.

**N 재계산**
- `max_steps_finalized` 턴은 N 재계산 입력에서 제외한다(성공 task로 섞이면 연속 무정보 횟수 최댓값이 부풀어 N이 커짐). 코드: `evalrun/regrade.py`(`regrade --n`)·`evalrun/explore.py`. 성공 task 판정이 `status`만 보는지 확인하고 필요하면 `stop_reason` 제외를 넣는다.
- **회귀 테스트**: 과거 dev 실행(dev3~dev5, `data/results/dev/` 원자료)으로 재계산한 값이 변경 전과 같다. `n-registration-m5.md`와 결과 문서의 값은 수정하지 않는다.
- 수정 허용 경로에 `evalrun/regrade.py`와 regrade 소비자 테스트를 "~에 한함, 단언 약화 금지"로 넣는다.

**평가 기록 영향**
- 프롬프트 해시 불변(시스템 프롬프트·도구·모델·파라미터 그대로). 골든 14개 재기록 없음: `test_golden_m3`·`test_regolden`·`test_golden_trace` 수정 없이 통과해야 한다.
- 골든 판정("TCR 통과 + 기대 일치")은 변동 없음. dev 재실행 없이 사전 등록만 한다(결정 1).

**dev·골든이 8단계에 닿지 않음 — 기계 검증**
- 코드상 관계: 도구 호출 없는 응답은 곧바로 `completed`라 `route_model`이 END로 보낸다(`graph.py:419-425,799-801`). `model`로 되돌아오는 경로는 `read_tools`(`818-820`)와 유효하지 않은 쓰기 초안(`propose`, `833-839`)뿐이고, 둘 다 직전 assistant 메시지에 도구 호출이 있을 때만 열린다. 따라서 `steps`(= 모델 호출 수) ≤ 도구 호출을 낸 모델 호출 수 + 1. 관찰의 `tool_calls`는 실행된 읽기만 세므로 근거로 쓰지 않고 trace의 `model_call` span 수와 task span `steps`를 원자료로 쓴다.

| 대상 | 실행 ID | trace 수 | 최대 모델 호출 | task `steps` 최대 |
|---|---|---|---|---|
| dev5 (writes_team) | 02ef5d5a… | 20 | 5 | 5 |
| dev4 | d2cd927c… | 20 | 4 | 4 |
| dev4 (다른 실행) | 710d8b6e… | 20 | 5 | 5 |
| dev3 | e3658977… | 20 | 5 | 5 |
| 그 이전 dev | 26c85539… | 20 | 4 | 4 |
| 그 이전 dev | da337bb7… | 20 | 4 | 4 |
| M2 | 8c18df86… | 7 | 4 | 필드 없음 |
| 골든 14개 | `data/replay/llm/*.jsonl` | 14 | 5 (T5-01) | 헤더 `call_count`와 기록 행 수 14개 모두 일치 |

(`data/replay/golden/`에는 `expectations.json`·`status.json`만 있고 녹화는 `data/replay/llm/`에 있다.)

**사전 등록 `hypotheses-m6-finalize.md`** (phase 실행 전 커밋, Codex는 읽기만)
- 변경 이유와 근거 경로(`2026-10-09-m5-dev5-live.md` §알려진 한계, trace `e5d25ab4…`), 안내문 sha256, 위 불변식과 표.
- "TCR·정확도 주장은 하지 않고, 공식 test는 아직 실행하지 않았다"는 명시.
- 관찰 기준: 같은 질문 5회 반복에서 근거가 있을 때 `failed/max_steps` 발생 수(목표 수치 없음).
- test(held-out) 실행 때 `max_steps_finalized` 건수를 따로 보고. N 재계산 입력에서 `max_steps_finalized` 턴 제외.

**UI**: 새 뷰 필드 `limited: true`(안전한 한 필드). `stop_reason` 비노출 단언은 유지. 완료 턴 아래에 "조사 단계 한도(8단계)에서 멈추고 모은 근거로 답했습니다. 일부 내용이 빠졌을 수 있습니다." `failed/max_steps` 문구는 #122에서 처리됨.

### 1-2. 대화 삭제 (소프트)

- **표시**: 대화의 **모든 턴 행**(행 키가 `turn_id`)에 같은 트랜잭션으로 `deleted_at`을 찍는다(`UPDATE … WHERE conversation_id=%s AND owner_sub=%s` 한 번). 컬럼은 `ALTER TABLE … ADD COLUMN IF NOT EXISTS deleted_at TEXT NULL`, 이전 스키마 테이블 테스트와 `make test-m5-pg` 케이스. 인메모리 구현도 같은 동작.
- `list_for_owner`·`conversation_of`·`awaiting`은 삭제된 행을 제외.
- **서비스** `delete_conversation`: 대화 잠금 → `_expire` → task 잠금(잠금 순서 고정). 남의 대화·없는 대화·이미 삭제된 대화·형식 오류 ID는 모두 기존 `404 conversation_not_found`(본문 동일). 승인 대기·`interrupt` 있음은 기존 `409 approval_pending`, 실행 중은 기존 `409 turn_in_progress`(새 코드 없음). 만료된 승인은 `_expire`가 먼저 닫아서 삭제 가능.
- **순서**: `conversation.deleted` 감사를 먼저 쓴다(실패하면 삭제하지 않음). 감사는 성공했는데 표시가 실패하면 **500**. 이때 재시도하면 같은 삭제의 감사 이벤트가 두 번 남을 수 있다 — `ARCHITECTURE.md`·알려진 한계에 적는다. 두 번째 정상 삭제는 404이고 이벤트를 남기지 않는다.
- `add_turn`·`conversation_view`·`progress`(SSE)·`GET /tasks/{id}`(두 번째 턴 이후 `turn_id` 포함)·`POST /tasks/{id}/approval`·`approvals`가 같은 잠금 안에서 삭제 여부를 확인하고 404.
- **감사 이벤트** `conversation.deleted`: `task_id=<대화 ID>`, `actor_sub=<요청자>`, 필드 없음(필드명 충돌 없음). `EVENTS` 정확 일치 테스트(`tests/test_audit_events.py`, `tests/test_audit.py`)는 소비자. `make audit-verify`·`smoke-audit` 경로 통과 확인.
- **보존 정리 포함**: `prune_checkpoints(saver, …)`에 선택 인자 `deleted_ids` 추가. 삭제 표시된 대화는 나이와 무관하게 후보. 단 `running`이거나 만료 전 `awaiting_approval`이면 활성으로 보존. `api/retention.py`가 턴 인덱스의 새 메서드 `deleted_conversation_ids`로 읽어 넘기고, 정리 후 기존대로 `delete_conversations`가 행을 지운다. 소비자: `agent/retention.py`·`api/retention.py` 기존 테스트, `make checkpoint-prune` 출력(마지막 줄·순서 단언이 있으면 새 값을 앞 줄에 둔다, FP-061).
- **UI 문구**(확인창·문서 동일): "원문은 다음 정리 때 제거되고, 감사 기록과 이미 발송된 효과는 남는다." 삭제 후 목록 새로고침. 승인 대기 중 삭제는 409 문구.
- **권한 우회**: 고정된 120건은 건드리지 않고 `hypotheses-m6-api-authz.md`에 결정론 pytest 프로브로 등록. 프로브: 남의 대화 404(본문이 없는 대화와 동일), 세션 없음 401, 형식 오류 ID 404, 승인 대기 409, 실행 중 409, 삭제된 대화에 대한 GET·SSE·턴·`/tasks/{id}`·`/tasks/{id}/approval` 404, 두 번째 턴 ID 접근 404, BFF Origin 불일치 403, `approvals` 목록 비노출.

### 1-3. 공유 폴링 훅 — 이번 범위에서 제외
사이드바 승인 배지가 인박스 처리 직후 최대 10초 늦게 갱신되는 한계는 `ARCHITECTURE.md` 알려진 한계에 한 줄로 적는다.

## 2. Phase 구성

| phase | Codex 구현 | 사용자 live | 비고 |
|---|---|---|---|
| **m6-limits-delete** (9 step) | step 0~8 연속 | 아래 4절 | 공식 test 전에 머지되어야 한다(그래프 변경) |
| m6-guardrail-v2 | v2 분류기, 모델·단가 등록, 스모크 코드 | Haiku 모델 ID·가격 확인 → `make agent-key-update`, `make team-keys-update` → v2 스모크 → `make obs-up`, `make smoke-guardrail` | v2 호출 위치는 v2 ADR로 phase 파일 작성 전에 확정 |
| m6-redteam-run | 레드팀 live 러너, 집계, 골든 Guardrail 판정 비교, test 40 실행 가드 | 7절의 순서 | 7절 등록 커밋 뒤에만 정상 대조 test 40 실행 |

**m6-limits-delete step**
0. `audit-conversation-deleted` — `audit/log.py`, EVENTS 소비자 테스트
1. `turn-index-soft-delete` — `api/conversations.py`, 전체 턴 행 표시, `deleted_conversation_ids`, 이전 스키마 테스트, `tests/test_m5_pg.py` 사용자 실행 케이스
2. `retention-deleted-ids` — `agent/retention.py`, `api/retention.py`, 소비자 테스트
3. `delete-service` — `api/tasks.py`
4. `delete-route-entrypoint` — `api/app.py`의 `DELETE /conversations/{id}`, `tests/test_api_entrypoint.py` 라우트 표 갱신(실제 진입점 경유, FP-070), 권한 우회 프로브
5. `web-delete-ui` — BFF route handler, 확인창, 목록 갱신, 삭제 e2e 스펙(`make e2e-web`은 사용자 실행)
6. `finalize-on-step-limit` — `agent/graph.py`, `agent/turns.py`, `evalrun/regrade.py`·`explore.py`, 관련 테스트
7. `limited-notice` — API 뷰 `limited`, BFF 허용 목록, 화면 문구
8. `docs-sync` — ARCHITECTURE·THREAT_MODEL·ADR 관련 항목(`docs/results`는 건드리지 않음)

## 3. 작업 트리와 사전 등록 순서

현재 `data/manifest/costs.jsonl` 12줄이 수정 상태이고 이 문서가 untracked다. 모두 사전 등록 커밋에 함께 넣어 main을 clean으로 만든 뒤 `execute.py`를 실행한다(원장만 올리는 PR은 만들지 않는다, FP-063). 사용자가 git 작업을 요청했을 때:

1. `feature/m6-prereg`에서 `docs:` 커밋 하나로 `hypotheses-m6-finalize.md`, `hypotheses-m6-api-authz.md`, `docs/M6_DESIGN_DRAFT.md`, phase 파일(`phases/m6-limits-delete/`, `phases/index.json`), `data/manifest/costs.jsonl`을 커밋한다.
2. PR → 머지 → main pull, clean 확인. 이 PR은 `make verify` 대상이다(생략 예외 아님).
3. `execute.py m6-limits-delete`를 `run_in_background`로 실행.

step 파일은 공통 금지사항 고정 블록을 복사하고, 쓰고 나서 `uv run python scripts/step_file_checks.py phases/m6-limits-delete/step*.md`로 점검한다.

## 4. 첫 live에서 실패할 수 있는 지점과 대응

| 과거 유형 | 이번 지점 | 대응 |
|---|---|---|
| 같은 커밋 2회 등록 | dev 재실행을 하지 않음 | 해당 없음(결정 1) |
| 러너 산출물이 골든을 덮어씀 | dev 미실행 | 골든 변경 0을 테스트로 확인 |
| 서버 진입점 결선 | 새 `DELETE` 라우트가 `create_app` 클로저 값을 붙잡을 위험 | step 4 진입점 경유 테스트, 라우트 표 정확 일치 |
| 가짜 의존성이 실제 계약 미재현 | 인메모리 턴 인덱스는 ALTER·잠금을 재현 못 함 | 이전 스키마 테스트, `make test-m5-pg` |
| step 지정 필드·전제가 실제 파일에 없음 | `stop_reason` 비노출 전제는 확인함 | step 파일 작성 때 `rg`로 재확인 |
| 앞 step 산출물 재생성 | 없음 | — |
| 새 응답 불변식 vs 현재 API | `limited`는 현재 뷰에 없음(step 7이 추가). 404 본문 동일성은 단일 `ConversationError`라 만족 | 프로브로 단언 |
| 소비자 테스트 누락 | EVENTS 단언 2개, 뷰 키 단언 3곳, `ui-boundary`, `project.test`, 라우트 표, `test_agent_turns` 키 집합, `test_agent_limits` 설정, retention 테스트·`checkpoint-prune` 출력, regrade 테스트 | 수정 허용 목록에 "~에 한함, 단언 약화 금지" |
| 같은 훅의 새 규칙 vs 기존 테스트 | a안 안내문이 Guardrail v1 규칙에 걸릴 위험 | `screen_request` 통과 단위 테스트(규칙 파일 내용은 Codex가 열람하지 않음) |
| 새 이벤트 필드명 충돌 | 필드 없음 | — |

live 전 남는 위험: 이미 있는 개발 DB(`govops_conversation_turns`)에서 ALTER가 동작하는지, 기존 대화의 `deleted_at`이 NULL로 보이는지.

step 점검 항목: 열람 금지 경로는 감사 쪽(step 0)에서 `data/interim/`만 선언하고 "열람 금지의 범위" 문단에 코드의 런타임 접근은 대상이 아님을 적는다(FP-059). 이 phase는 CLI 출력을 새로 만들지 않아 출력 마지막 줄 단언 충돌이 없다(`checkpoint-prune` 출력만 step 2에서 확인). 앞 step 테스트의 소비자: step 1이 삭제 필터를 모아 step 3이 인덱스 테스트를 바꾸지 않게 한다. 옮길 FP: 010·054·059·060·061·070.

## 5. m6-limits-delete 사용자 live (순서대로)

1. `make test-m5-pg`
2. `make serve` 재시작 후 브라우저: 개방형 질문 5회(platform·finance), 삭제(유휴·승인 대기 409·삭제 후 접근)
3. `make audit-verify`, `make smoke-audit`
4. 삭제 스펙을 추가했다면 `make e2e-web`(포트 3200·8200·8201 비워 둠)

예상 비용: 개방형 질문 1회 약 $0.002(trace `e5d25ab4…`의 8호출 $0.00204 기준) × 10회 ≈ $0.02. 나머지는 API 호출 없음. 결과 PR에 비용 원장을 함께 넣는다.

## 6. 비용 추정 (원장 기준)

- 한도: OpenAI $9.13, Anthropic $11.47, Gemini $7.29. 원장의 `cost_usd` 행 합계는 OpenAI $0.156, Anthropic $0.233(구형 행은 단위가 다를 수 있어 참고값).
- OpenAI 주 모델: dev 실측 $0.0128 / 20 task ≈ $0.0006~0.0007 / task. 레드팀 120건 + 정상 대조 60건(v1·v2 각 1회)은 약 $0.13 / 실행 수준.
- v2(Anthropic Haiku 계열): **단가 미확정 — 모델 ID·가격 확인 뒤 재추정한다.** 호출 수 추정만 적는다: 180 task × 평균 3~4 모델 호출(원자료 최대 5)에 분류기 호출이 한 번씩 붙는다.
- 공식 test 실행(7절)은 별도의 test 실행이 아니라 M6의 정상 대조 test 40건 실행이 대체한다. 따라서 M7 전에 별도 test 실행 비용은 잡지 않는다. 공식 test 실행은 task당 3회라 OpenAI 약 120 실행 × $0.0007 ≈ $0.09 수준이며, v2 분류기와 Gemini 채점 비용은 단가 확인 뒤 재추정한다.

## 7. 레드팀 정상 대조 = test 실행 문제 (결정)

**사실**: `hypotheses-m4-redteam.md:11,104-107`에 따라 정상 대조는 평가셋 60건(dev 20 + test 40)을 재사용하고, 오탐 정의에 "격리가 일어난 task"가 포함된다. 에이전트를 실제로 실행해야 하므로 이는 held-out test 40건의 실행이다. 이 등록은 바꾸지 않는다.

**결정**
1. **목표 등록 선행**: AGENTS의 "TCR·정확도·지연 목표는 dev 실행 뒤 test 실행 전에 `docs/results/`에 등록" 규칙에 따라, test 목표(TCR·정확도·지연)를 담은 사전 등록 파일 `docs/results/hypotheses-test-targets.md`를 **레드팀 live의 정상 대조 test 40건 실행 전에** 커밋한다. 수치는 이 초안이 정하지 않고 등록 때 사용자와 정한다.
2. **공식 test 실행 지정(결과를 보기 전 등록)**: M6 최종 구성(v2 + v1 대체 경로)의 test 40건 실행을 공식 test 결과로 한다(task당 3회, EVAL 규정). 다른 실행(v1 단독 등)은 참고로 함께 보고한다. v2가 비용 한도 초과 등으로 빠지면 공식 구성은 최종 구성(v1)으로 하며 이 조항도 같은 등록에 적는다. **v2를 공식 구성에서 뺄지는 정상 대조 test 40건 실행 전에 결정**하고 `hypotheses-test-targets.md`(또는 그 전 커밋)에 기록한다. 판단 근거는 스모크·레드팀 공격 120건·비용 결과만 쓰고, test 결과는 쓰지 않는다.
3. **동결 규칙(같은 등록)**: 공식 test 실행 뒤에는 그래프·프롬프트·도구 설명·Guardrail 규칙(v1 규칙 파일, v2 분류기 프롬프트·모델)을 바꾸지 않는다. 바꾸면 새 등록 + 재실행 + 이전 결과 보존 절차를 따른다. M7은 데모·기록 화면만 다룬다. 따라서 `m6-limits-delete`(a안)와 `m6-guardrail-v2`는 공식 test 실행 전에 머지되어 있어야 한다.
4. **보고**: 공식 test 실행에서 `max_steps_finalized` 건수를 따로 보고한다(`hypotheses-m6-finalize.md`).
5. **러너 가드**: `m6-redteam-run`의 러너는 `hypotheses-test-targets.md`가 커밋되어 있지 않으면 test 40건 실행을 거부한다(FP-043의 `hypotheses_not_committed`와 같은 방식).
6. 정상 대조 test 결과를 본 뒤 프롬프트·규칙을 조정하지 않는다.

**순서(phase 흐름에 반영)**
1. `m6-limits-delete` 머지 + live 확인
2. `m6-guardrail-v2` 머지 + 스모크 + `make smoke-guardrail`(Langfuse 포함)
3. `m6-redteam-run` Codex 구현 머지
4. 사용자: `hypotheses-test-targets.md` 작성·커밋(PR 머지, 수치·공식 실행 지정·동결 규칙 포함)
5. 사용자 live: 레드팀 120건 v1 → v2, 정상 대조 60건(test 40 포함: 공식 test 실행은 v2 구성 3회, v1은 참고), 판정 비교
   - 레드팀 공격 120건은 test와 무관하므로 4번 전에 실행해도 되지만, 정상 대조 test 40건은 4번 뒤에만 실행한다.

## 8. 결정 기록

1. a안은 dev 재실행 없이 사전 등록만(1-1의 기계 검증 표가 근거).
2. 실행 중 삭제도 409(`turn_in_progress`), 승인 대기는 `approval_pending` 재사용.
3. 공유 폴링 훅은 이번 범위에서 제외, 알려진 한계에 한 줄.
4. v2 분류기 호출 위치는 v2 ADR로 phase 파일 작성 전에 확정. 기준은 게이트웨이 우회 호출이 v2를 거치지 않는 경로가 생기는지(위협 모델). import 허용 목록 테스트는 맞춰야 할 제약. 사전 검사 오류·타임아웃은 차단(fail-closed) 유지.

## 9. 유지 사항

골든 재기록 없음, 레드팀 120건 고정, 권한 우회 프로브 별도 등록, `stop_reason` 비노출 단언 유지 + `limited` 필드, `conversation.deleted` 무필드 이벤트, `ALTER … IF NOT EXISTS` + 이전 스키마 테스트, `step_file_checks` 점검.
