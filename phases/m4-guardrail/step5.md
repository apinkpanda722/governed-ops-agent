# Step 5: agent-blocked-input

## 읽어야 할 파일

- `/AGENTS.md`(계층 경계 표의 `agent/`·`api/`·`audit/` 행, 감사·토큰 관련 CRITICAL), `/docs/ADR.md`(ADR-008·ADR-012·ADR-018 6항), `/docs/THREAT_MODEL.md`(V1·V2)
- 이전 step 산출물: `/src/govops/gateway/client.py`(`GuardrailBlocked`·`GuardrailUnavailable`·`ChatResult.guardrail`), `/src/govops/gateway/hook_header.py`(`GuardrailInfo`: `rules`·`code`·`team`·`pre`·`c`·`q`·`m`·`ids`·`bad`·`trunc`·`ms`·`post`)
- `/src/govops/agent/graph.py`(`AgentState`, `model_body`의 예외 처리, `route_model`, `finish`, `record`, `run_task`의 초기 상태), `/src/govops/api/tasks.py`(`_view`, `TaskService._run`), `/src/govops/api/app.py`, `/src/govops/audit/log.py`(`EVENTS`·`REASONS`·`_validate`·`MemoryAuditLog`·`append_with_retry`), `/src/govops/obs/trace.py`(`set_attributes`)
- `/tests/test_agent_graph.py`, `/tests/test_api_tasks.py`, `/tests/test_audit.py`, `/tests/test_architecture.py`
- `/.claude/skills/failure-patterns/SKILL.md`(FP-010·041·044) — 읽기만 한다
- **외부 계약 실측**(원자료: 위 파일들, 성공 여부와 반환 내용을 따로 적는다):

| 지점 | 성공 여부 | 반환·효과 내용 |
|---|---|---|
| `AuditLog.append(event, *, task_id, actor_sub, **fields)` | 허용 이벤트만 성공 | `EVENTS` 밖의 이벤트, 필드 이름 `_FORBIDDEN`(`text`·`body`·`content`·`title`·…), 값이 해시(64 hex)·mock ID·등록된 열거 밖의 문자열이면 PII 검사 후 거부. `tool`·`decision`·`check_result`·`reason` 키는 각자의 열거 집합만 허용 |
| 모델 호출 실패 | `graph.model_body` | `BudgetExceededError`·`LedgerMissingError`·`GatewayError`는 `status: failed`와 `error`(`budget_exceeded`·`ledger_missing`·`gateway_error`), 그 밖은 `model_error` |
| `route_model` | `status != "running"`이면 `END` | 새 종료 상태는 `finish` 노드를 거치지 않고 그대로 끝난다 |
| `ChatResult.guardrail`(이전 step) | 헤더가 있을 때 `GuardrailInfo` | 호출마다 이번 요청의 격리 수 `q`, 마스킹 수 `m`, 격리된 `tool_call_id`(`ids`, 최대 64, `trunc`)와 형식 밖 개수 `bad`. **tool 메시지는 매 모델 호출마다 다시 전송되므로 같은 격리가 호출마다 다시 보고된다** |

## 작업

**수정할 파일**(이 목록이 전부다): `src/govops/agent/graph.py`, `src/govops/api/tasks.py`, `src/govops/audit/log.py`, `tests/test_agent_graph.py`·`tests/test_api_tasks.py`·`tests/test_audit.py`·`tests/test_agent_guardrail.py`(신규)·`tests/test_api_guardrail_view.py`(신규). 위 변경으로 깨지는 기존 테스트 기대값 갱신은 허용하되 단언을 약화하지 않는다. 계층: `agent/`는 `broker`·`mocks`·`eval`·LLM SDK를 import할 수 없고, 모델 호출은 `gateway` 클라이언트로만 한다(`from govops.gateway.client import GuardrailBlocked, GuardrailUnavailable`은 허용). `api/`는 `broker`·`mocks`·LLM SDK를 import할 수 없다. TDD.

**목표**: ① 입력 차단을 **새 종료 상태 `blocked_input`**으로 구조화해 API가 돌려준다(상태·사유 코드·사용자 안내 문구만, 입력 원문과 세부 판정 근거는 응답·로그에 없음). ② Guardrail 훅의 오류·타임아웃은 `blocked_input`이 **아니라** `failed` + `error: "guardrail_unavailable"`(그 밖에 `team_unknown`·`guardrail_header_missing`·`guardrail_stale`도 같은 방식)이다. ③ 차단·격리·마스킹·출력 경보·훅 오류를 감사 로그에 남긴다(원문 없이 해시·코드·건수만). ④ 격리 건수는 `tool_call_id` 기준 중복을 제거한다.

### 1. 상태와 그래프 (`agent/graph.py`)

- `AgentState.status`에 `"blocked_input"`을 추가하고, 상태 키를 추가한다: `quarantined_ids: list[str]`(격리된 `tool_call_id`의 sha256 hex 64자 목록), `quarantine_unmatched: int`, `masked_total: int`, `guardrail_truncated: bool`, `guardrail_block: dict | None`(`{"code": ...}`만). `run_task`의 초기 상태에 기본값(`[]`·`0`·`0`·`False`·`None`)을 넣는다. 이전 체크포인트(새 키 없음)도 읽혀야 한다(`state.get(…)`).
- `model_body`: `GuardrailBlocked`를 먼저 잡아 `{"steps": steps, "status": "blocked_input", "error": exc.code, "guardrail_block": {"code": exc.code}}`를 돌려준다(`answer`는 `None`). `GuardrailUnavailable`은 `{"steps": steps, "status": "failed", "error": exc.code}`다. 둘 다 기존 `GatewayError` 분기보다 앞에 둔다. 새 종료 상태이므로 `route_model`은 이미 `END`로 보낸다. `finish` 노드의 문구 사전에는 이 상태가 도달하지 않음을 확인하고 필요하면 한 항목 추가한다.
- **차단된 입력은 이후 맥락에 넣지 않는다**: `blocked_input`으로 끝난 task의 `messages`에 모델 응답을 붙이지 않는다(이 step에서는 `messages`를 그대로 두면 된다).
- 모델 호출 성공·차단 모두에서 `ChatResult.guardrail`(또는 `GuardrailBlocked.info`)이 있으면 trace 속성(`set_attributes`)에 **안전한 값만** 남긴다: `guardrail.pre`·`guardrail.q`·`guardrail.m`·`guardrail.ms`·`guardrail.team`·`guardrail.rules`·`guardrail.code`(훅 코드 해시 앞 12자). 입력·메시지·`tool_call_id` 원문은 trace에 넣지 않는다.
- **격리 건수 중복 제거**: 성공 응답마다 `info.ids`의 각 `tool_call_id`에 대해 `sha256(id)`가 `state["quarantined_ids"]`에 없을 때만 새로 격리된 것으로 센다(두 접두어 `call_…`·`toolu_…`가 섞여도 같다). `info.bad`는 `quarantine_unmatched = max(기존, info.bad)`. `info.m`은 `masked_total = max(기존, info.m)`. `info.trunc`가 1이면 `guardrail_truncated = True`(격리 건수는 "이상"). task 전체 격리 건수 = `len(quarantined_ids) + quarantine_unmatched`. 같은 격리를 다음 호출이 다시 보고해도 건수·감사 기록은 늘지 않는다(테스트로 고정).

### 2. 감사 이벤트 (`audit/log.py`)

- `EVENTS`에 추가: `guardrail.input_blocked`, `guardrail.tool_quarantined`, `guardrail.tool_masked`, `guardrail.output_flagged`, `guardrail.error`.
- 필드 검증 규칙 추가(기존 규칙은 약화하지 않는다): `code`는 `GUARDRAIL_CODES = {"injection_direct","pii_input","guardrail_unavailable","team_unknown","guardrail_header_missing","guardrail_stale","guardrail_post_error"}`만 허용, `input_sha256`·`tool_call_sha256`은 64 hex 해시 형식이고 **`input_sha256`은 `code == "injection_direct"`인 `guardrail.input_blocked`에서만 허용한다(`pii_input`·다른 이벤트에 있으면 거부)**, `rules_digest`·`code_digest`는 `^[0-9a-f]{12}$`, `team`은 `^[a-z][a-z-]{1,20}$`, `count`·`truncated` 등 정수·불리언은 기존 규칙대로. **식별자·해시·코드 필드는 PII 정규식을 돌리지 않고 형식으로만 검증한다**(FP-010: 무작위 hex가 전화번호 규칙에 걸려 거부되는 결함의 재발 방지. 무작위 대량 테스트를 둔다).
- 기록 규칙(그래프 안, 호출 결과를 받은 직후): 입력 차단 → `guardrail.input_blocked`(`code`, `rules_digest`, `code_digest`, 그리고 **`code == "injection_direct"`일 때만** `input_sha256` = 사용자 요청 문자열의 sha256. **`pii_input`에는 `input_sha256`을 남기지 않는다**: 짧은 요청의 PII는 해시로 되돌릴 수 있다), 새 격리 → `guardrail.tool_quarantined`(`tool_call_sha256`, `count=1`), 마스킹 건수 증가 → `guardrail.tool_masked`(`count`=증가분), `post=flag` → `guardrail.output_flagged`, `post=error`와 `GuardrailUnavailable` → `guardrail.error`(`code`). **감사 쓰기 실패는 차단·격리 결과를 바꾸지 않는다**: 이미 일어난 방어 결과는 그대로이고, 실패는 trace 속성 `guardrail.audit_gap=True`로만 경보한다(부작용 실행 전 의도 기록과 달리 이 이벤트는 사후 기록이다). 사용자 입력·도구 결과 원문은 어떤 필드에도 없다.

### 3. API 응답 (`api/tasks.py`)

- `_view`: `status == "blocked_input"`이면 `block: {"code": <code>, "message": <안내 문구>}`를 추가한다. 문구는 코드별 고정 한국어 문장이다: `injection_direct` → "요청에 시스템 지시를 바꾸려는 표현이 있어 처리하지 않았습니다. 업무 내용만 다시 입력해 주세요.", `pii_input` → "요청에 개인정보로 보이는 값이 있어 처리하지 않았습니다. 개인정보를 제거하고 다시 요청해 주세요." 문구는 **어떤 규칙에 걸렸는지·입력의 어느 부분인지·패턴을 드러내지 않는다.** 이 상태에서 `answer`는 `None`이고 `cited_doc_ids`·`observed_doc_ids`는 비어 있다.
- 모든 뷰(`blocked_input` 포함)에 `guardrail` 요약을 추가한다: `{"quarantined": <건수>, "quarantined_at_least": <trunc 여부>, "masked": <건수>}`(UI의 "참고 문서 일부 격리" 표시용. 격리 0건이면 0). 훅 오류 종료는 `status: "failed"`, `error: "guardrail_unavailable"`(또는 `team_unknown` 등)이며 `block` 키는 없다(UI 입력 차단 카드에 나오지 않는다, 테스트로 고정).
- 승인자 뷰(`approval=True`)에는 `guardrail`·`block`을 넣지 않는다(요청자의 입력 정보를 승인자에게 보이지 않는다).
- `get()`·`approve()`의 소유자 규칙은 바꾸지 않는다. 종료 상태로서 `blocked_input`은 `failed`와 같이 취급한다(체크포인트 보존 기간·`startup_cleanup`은 `running`만 `interrupted`로 바꾸므로 영향 없음을 테스트로 확인).

### 4. 테스트

- 그래프(가짜 `ChatClient`): 입력 차단 → 모델 호출 1회 이후 도구 호출 0·부작용 0·`blocked_input`·감사 1건, 훅 오류(`GuardrailUnavailable`) → `failed`·`guardrail_unavailable`·`blocked_input`이 아님, `ChatResult.guardrail`로 격리 보고 → 감사·건수, **같은 `tool_call_id`를 두 번 보고하면 건수 1**, `call_`·`toolu_` 혼합 + 64 초과(`trunc`) → "이상" 표시, 감사 쓰기 실패 주입(가짜 감사)에서도 차단 상태 유지와 경보 속성.
- API: `blocked_input` 응답 형태(코드·문구만, 입력 원문 부재를 응답 JSON 전체 문자열 검색으로 확인), 훅 오류에 `block` 키 없음, 다른 사용자의 task 조회는 404, 승인자 뷰에 `guardrail`·`block` 없음.
- 감사: `injection_direct` 차단은 `input_sha256`을 남기고 `pii_input` 차단은 해당 필드가 아예 없음(기록된 줄에서 확인), `pii_input`에 `input_sha256`을 넣으면 거부, 5개 새 이벤트의 정상 기록, 열거 밖 `code`·형식 밖 해시·`content` 필드 거부, 무작위 hex(`uuid4().hex` 10,000개, 시드 고정)가 식별자 필드로 통과, 기존 이벤트 테스트는 그대로 통과.

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

- 훅 오류·타임아웃을 `blocked_input`으로 분류하지 마라. 이유: 입력 차단 카드는 사용자 입력이 규칙에 걸린 경우만이고, 인프라 장애를 사용자 탓으로 보이게 하면 안 된다. `failed` + `guardrail_unavailable`이다.
- 입력 원문·도구 결과 원문·매치 문자열·`tool_call_id` 원문을 감사·trace·API 응답·로그에 넣지 마라. `pii_input` 차단에는 입력 해시도 남기지 마라. 이유: 감사는 해시·코드·건수만 남기지만(ADR-012) 짧은 PII 입력의 해시는 후보를 대입해 되돌릴 수 있다. 응답은 상태·코드·고정 문구만 가진다.
- 식별자·해시·코드 필드에 PII 정규식을 돌리지 마라(FP-010). 이유: 무작위 hex가 전화번호·카드 규칙에 걸려 정상 task가 `audit_write_failed`로 끝난다. 형식으로만 검증하고 무작위 대량 테스트를 둔다.
- 새 상태를 기존 상태로 매핑하지 마라(FP-041). 이유: `blocked_input`을 `failed`·`rejected`로 합치면 다른 판정(예: 승인 거부 성공 조건)이 우연히 통과한다. 평가 쪽 매핑은 다음 step이 별도 범주로 한다. 이 step은 `evalrun/`·`eval/`을 고치지 않는다.
- 같은 `tool_call_id`의 격리를 호출마다 다시 세어 건수·감사 기록을 늘리지 마라. 이유: tool 메시지는 매 모델 호출에 다시 실려 같은 격리가 반복 보고된다(실측). task 단위 집합으로 한 번만 센다.
