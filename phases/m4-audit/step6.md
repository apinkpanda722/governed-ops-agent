# Step 6: model-call-event

## 읽어야 할 파일

- `/AGENTS.md`(계층 경계 표의 `agent/` 행, 아키텍처 규칙), `/docs/ADR.md`(ADR-012, ADR-019, ADR-023), `/docs/ARCHITECTURE.md`
- 이전 step 산출물: `/src/govops/audit/log.py`(`model.call` 필드 규칙: step 1), `/src/govops/audit/crypto.py`(`format_usd`)
- `/src/govops/agent/graph.py`(`model_body`의 `chat.complete` 호출 앞뒤, `guardrail_record`, `guardrail_trace`, `set_attributes` 사용), `/src/govops/gateway/client.py`(`ChatResult`: `deployment_id`·`model_group`·`fallback_used`·`cost_usd`·`guardrail`), `/src/govops/gateway/hook_header.py`(`GuardrailInfo`: `team`·`pre`·`post`·`q`·`m`)
- `/tests/test_agent_guardrail.py`·`/tests/test_agent_graph.py`(`FakeChat`, `reply`, `MemoryAuditLog` 사용), `/tests/test_agent_scope.py`
- `/.claude/skills/failure-patterns/SKILL.md`(FP-010·012·020·060·061) — 읽기만 한다

## 작업

**수정할 파일**(이 목록이 전부다): `src/govops/agent/graph.py`, `tests/test_agent_model_call_event.py`(신규), `tests/test_agent_guardrail.py`·`tests/test_agent_graph.py`·`tests/test_agent_scope.py`·`tests/test_api_tasks.py`·`tests/test_effects_matrix.py`·`tests/test_evalrun_driver.py`·`tests/test_e2e_isolation.py`·`tests/test_golden_m3.py`·`tests/test_evalrun_dev_m3.py`(성공한 모델 호출마다 `model.call` 레코드가 추가되는 것에 맞춘 이벤트 목록·개수·인덱스 단언의 갱신에 한함, 단언 약화 금지). 계층: `agent/`는 `broker`·`mocks`·`eval`·LLM SDK를 import할 수 없다(`govops.audit`는 허용). TDD.

열람 금지 경로: `data/interim/`
수정 허용 경로: `src/govops/agent/graph.py`, `tests/test_agent_model_call_event.py`, `tests/test_agent_guardrail.py`, `tests/test_agent_graph.py`, `tests/test_agent_scope.py`, `tests/test_api_tasks.py`, `tests/test_effects_matrix.py`, `tests/test_evalrun_driver.py`, `tests/test_e2e_isolation.py`, `tests/test_golden_m3.py`, `tests/test_evalrun_dev_m3.py`

**배경(ADR-012)**: 모델 호출마다 task·사용자·팀 키·모델·fallback 여부·비용·Guardrail 판정을 최소 기록한다. 원문(프롬프트·응답·도구 인자)은 남기지 않는다. 기록 대상은 **`chat.complete`가 결과를 돌려준 호출(성공)** 뿐이다. 차단된 호출은 `guardrail.input_blocked`가, 실패한 호출은 비용 원장·trace가 기록한다(이월 항목이므로 이 step에서 만들지 않는다).

### 1. `model_body`에 기록 추가

- `chat.complete`가 성공하고 결과를 받은 직후(Guardrail 격리·마스킹 처리 전)에 `audit`이 있으면 `model.call`을 기록한다. `audit`이 `None`(읽기 전용 프로필)이면 건너뛴다.
- 필드(모두 형식이 step 1에서 고정됨):
  - `team`: 결과의 `guardrail.team`이 있으면 그 값(인증 객체 팀: 가장 신뢰할 수 있는 출처), 없으면 `users[user_id].team`(`users`가 있고 사용자가 있을 때), 그것도 없으면 `"none"`.
  - `model`: `result.model_group`이 `^agent-[a-z-]{1,30}$`이면 그 값, 아니면 `"unknown"`.
  - `deployment_id`: `result.deployment_id`가 `^[a-z0-9][a-z0-9.-]{0,63}$`이면 그 값, 아니면 `"unknown"`. (게이트웨이 응답 본문의 `model`은 별칭·제공자 ID가 섞여 신뢰할 수 없다. 배포 ID 헤더가 신뢰할 신호다: FP-020.)
  - `fallback`: `bool(result.fallback_used)`.
  - `cost_usd`: `govops.audit.crypto.format_usd(result.cost_usd)`(float을 그대로 쓰지 않는다).
  - 결과에 `guardrail` 정보가 있으면 `guardrail_pre`=`info.pre`, `guardrail_post`=`info.post`, `guardrail_q`=`info.q`, `guardrail_m`=`info.m`을 **네 개 모두** 넣고, 없으면 네 개 모두 뺀다.
- 기록 실패(예외 전부)는 **fail-open**: 이미 모델 호출과 비용 정산이 끝났고 부작용이 아니므로 task를 중단하지 않는다. `set_attributes(**{"model.audit_gap": True})`로만 표시한다(예외 메시지·입력을 trace·로그에 남기지 않는다. `guardrail_record`와 같은 방식).
- **비동기 노드를 막지 않는다**: 감사 `append`는 동기 파일 I/O이고 잠금 마감(10초)까지 기다릴 수 있으므로 `await asyncio.to_thread(audit.append, ...)`로 호출한다.
- 기존 `guardrail_record`(동기)·부작용 기록 경로는 바꾸지 않는다.

### 2. 테스트

- `tests/test_agent_model_call_event.py` (`MemoryAuditLog`와 실제 `AuditLog(임시)` 둘 다):
  - 정상 호출 1회 → `model.call` 1건, 필드가 위 규칙대로이고 `cost_usd`가 `^\d+\.\d{6}$`, `fallback`이 `bool`.
  - fallback 결과(`fallback_used=True`, 다른 `deployment_id`)·`guardrail`이 `None`인 결과(필드 네 개 없음)·`model_group`이 별칭 형식이 아닌 결과(`"unknown"`)·`deployment_id`가 형식 밖(`"unknown"`).
  - 모델 호출이 여러 번(도구 호출 루프)이면 호출마다 한 건이고 `task_id`·`actor_sub`가 task의 것이다.
  - 원문 없음: 요청 문장·응답 본문·도구 인자가 레코드 JSON 어디에도 없다(`json.dumps(records)` 스캔).
  - 실패한 호출(`GuardrailBlocked`·`GuardrailUnavailable`·`GatewayError` 계열)은 `model.call`을 만들지 않는다(기존 `guardrail.*` 레코드만).
  - 감사 쓰기 실패(`append`가 `AuditWriteError`)여도 task가 같은 결과로 끝나고 `"model.audit_gap": True` 속성이 기록된다.
  - **이벤트 루프 비차단(FP-012)**: 감사 `append`가 `threading.Event.wait(timeout=5)`로 막혀 있는 가짜 sink와, 같은 루프에서 그 Event를 `set()`하는 동시 코루틴을 함께 돌린다. 노드가 루프를 막았다면 Event가 제때 설정되지 않아 5초 대기가 만료된다: 만료되지 않고 task가 끝남을 단언한다(고정 `sleep`에 기대지 않는다).
  - 읽기 전용 프로필(`audit=None`)에서는 아무 기록도 시도하지 않는다.
- `tests/test_agent_guardrail.py` 등 소비자 테스트: 성공 호출 뒤 `audit.records[0]`·`len(audit.records) == 1`처럼 위치·개수로 이벤트를 단언하던 곳을 **이벤트 이름으로 거른 목록**(예: `model.call` 제외)으로 바꾼다. 같은 이벤트를 같은 필드 값으로 계속 단언해야 하며 단언 내용을 지우지 않는다. 추가로 이 테스트들에서 `model.call`이 정확히 성공 호출 수만큼 있는지 한 줄 단언을 더해도 된다.

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


- 프롬프트·응답·도구 인자·요청 문장을 `model.call`에 넣지 마라. 이유: 지울 수 없는 로그에 원문을 넣으면 삭제 요구에 응할 수 없다. 형식이 정해진 값만 남긴다.
- 비용을 float 그대로 기록하지 마라. 이유: 정규화 JSON이 float을 거부하고 부동소수 표기가 환경마다 다를 수 있다. `format_usd`의 고정 6자리 문자열을 쓴다.
- `model`·`deployment_id`를 응답 본문 `model`에서 가져오지 마라. 이유: 실측에서 본문 `model`은 별칭과 제공자 ID가 섞여 일관되지 않았다. 배포 ID 헤더에서 온 `deployment_id`와 `model_group`을 쓴다(FP-020).
- `model.call` 감사 실패로 task를 중단시키지 마라. 이유: 모델 호출은 이미 끝났고 비용이 정산됐다. 부작용 경로의 fail-closed와 다르다. 경보 속성만 남긴다.
- 소비자 테스트의 이벤트 단언을 삭제·완화하지 마라. 이유: 기록 대상이 하나 늘었을 뿐 기존 이벤트 의무는 같다. 위치·개수 단언은 이벤트 이름 기준으로 바꾼다(FP-060·061).
