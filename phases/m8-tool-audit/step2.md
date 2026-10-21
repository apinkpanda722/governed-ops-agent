# Step 2: graph-tool-audit

## 읽어야 할 파일

먼저 아래 파일들을 읽고 프로젝트의 아키텍처와 설계 의도를 파악하라:

- `/AGENTS.md`(CRITICAL: 토큰·키를 state·checkpoint·trace·LLM 컨텍스트에 두지 않음, 대기 interrupt 규칙)
- `/docs/results/hypotheses-m8-tool-audit.md`(2절 경로 대응 표, 3·6절, 읽기만 한다)
- `/docs/M8_TOOL_AUDIT_DRAFT.md`(3·6절)
- `/src/govops/agent/graph.py`(`build_graph` 199~213행, `model.call` 기록 428~466행의 `asyncio.to_thread`·`model.audit_gap` 선례, 마무리 단계 506~511행, `read_tools_body` 522~582행, `propose` 677~705행, `route_model` 941~948행)
- `/src/govops/audit/toolcall.py`·`/src/govops/audit/argmac.py`·`/src/govops/audit/log.py`(step 1)
- `/tests/test_agent_model_call_event.py`(`sink`·`records` 픽스처, `test_audit_failure_is_content_free_and_fail_open` 163행~의 trace 속성 확인 방식, `test_append_does_not_block_event_loop` 186행~)
- `/tests/test_agent_graph.py`(`FakeChat`·`FakeTools`·`reply`·`call`), `/tests/test_m7_freeze.py`
- `/.claude/skills/failure-patterns/SKILL.md` — 읽기만 한다

## 작업

**수정할 파일**(이 목록이 전부다): `src/govops/agent/graph.py`(`tool_audit` 인자와 기록 지점 3곳 추가에 한함), `tests/test_agent_tool_audit.py`(신규), `tests/test_m7_freeze.py`(`EXPECTED`의 `"src/govops/agent"` 값 한 줄 갱신에 한함). TDD.

열람 금지 경로: `data/interim/`, `data/eval/`, `data/redteam/`, `src/govops/guardrails/rules_v1.json`, `.env`
수정 허용 경로: `src/govops/agent/graph.py`, `tests/test_agent_tool_audit.py`, `tests/test_m7_freeze.py`
열람 금지의 범위: 이 금지는 **Codex 세션이 해당 경로의 파일을 직접 열람·출력·인용하는 것**에 대한 것이다. 이 step의 코드가 런타임에 그 경로를 다루는 것(예: 기본 감사 디렉터리 `data/interim/audit`, 기본 감사 키 경로 `data/interim/audit_ed25519.pem`, `.env`에서 오는 환경 변수 `AUDIT_ARGS_HMAC_KEY`)은 대상이 아니다. 테스트가 `data/interim/`·`data/eval/`·`data/redteam/`의 실제 파일 **내용**을 읽는 것은 여전히 금지이며 테스트는 임시 디렉터리·`tests/conftest.py`의 임시 감사 키·합성 HMAC 키만 쓴다. 예외로 `data/results/`의 실행 디렉터리는 파일 이름과 JSON 키 이름을 확인하는 데 한해 열람할 수 있고, 응답 본문(`answer`·`effect_bodies`)이나 근거 본문을 출력·인용하지 않는다. 이 제한은 이 step이 새로 작성하는 테스트에 한한 것이며, 기존 `tests/test_m7_freeze.py`가 `scripts/freeze_digest.py`로 해시를 계산하며 동결 경로 파일을 바이트로 읽는 것은 `make verify`의 일부이므로 허용한다. Codex 세션이 그 파일 내용을 출력·인용하는 것은 여전히 금지다.

**목적**: 기록기가 주입된 그래프에서 모델이 낸 tool_call마다 감사 레코드를 하나 이상 남긴다(등록 2절). 기록기가 없으면 지금과 똑같이 동작한다.

### 1. `build_graph(..., tool_audit=None)`

키워드 인자 `tool_audit: ToolCallAuditor | None = None`을 더한다. `None`이면 `tool.call`을 남기지 않는다(그래프를 직접 조립하는 기존 테스트의 정확 일치 단언이 그대로 통과해야 한다: `tests/test_agent_graph.py` 581행, `tests/test_agent_semantic.py` 155행). `graph.agent_tool_audit`에 저장하지 않는다(키를 가진 객체를 그래프 속성으로 노출하지 않음).

### 2. 기록 지점 3곳 (등록 2절 표)

| 위치 | 상태 결정 | 기록 방식 |
|---|---|---|
| `read_tools_body`의 호출마다(도구 이름 span 안, `set_attributes` 전) | allowlist 밖 → `tool_not_available`; 인자 JSON 아님·dict 아님·신원 필드 → `invalid_argument`; 실행 후 `outcome.is_error` → `tool_error`; 실행 성공 → `ok`; 실행 중 예외 → `tool_unavailable`. `result_count = len(result_ids)` | `await asyncio.to_thread(tool_audit.record, …)`, 실패하면 `set_attributes(**{"tool.audit_gap": True})` |
| `propose`가 `invalid_argument` 응답을 만드는 분기(호출 2개 이상, 초안 있음, 스키마 오류) | 각 호출에 `proposal_invalid`, `result_count=0` | `propose`는 동기 노드이므로 직접 `tool_audit.record`, 실패는 삼킨다(부작용 없는 경로, fail-open) |
| 마무리 단계(`finalizing`)에서 모델 응답에 tool_call이 있어 `failed/max_steps`로 끝나는 분기 | 각 호출에 `not_executed`, `result_count=0` | `await asyncio.to_thread(…)`, 실패하면 `tool.audit_gap` |

- 주의: 실행된 도구가 오류 결과(예: `ToolRuntime`의 스키마 위반 `invalid_argument` 결과)를 돌려준 경우는 그래프 단계가 실행이므로 `tool_error`다. `invalid_argument`는 그래프가 실행 전에 거절한 경우만이다.
- `task_id=state["task_id"]`, `actor_sub`는 `read_tools_body`·마무리 단계에서 `runtime.context.user_id`, `propose`에서 `state["user_id"]`(기존 `record` 헬퍼와 같다).
- 기록 실패(모든 예외)는 도구 실행·응답 메시지·state·라우팅을 바꾸지 않는다(fail-open, 등록 6절). 예외 문장을 trace·로그에 남기지 않는다.
- 올바른 쓰기 제안은 기존 `side_effect.draft`가 덮으므로 `tool.call`을 더하지 않는다. `check`·`approval`·`execute` 경로는 바꾸지 않는다.

### 3. 테스트 `tests/test_agent_tool_audit.py`

합성 키(`secrets.token_hex(32)`, `monkeypatch.setenv`)로 `load_args_mac()`과 `ToolCallAuditor(MemoryAuditLog(), mac)`을 만들고 `build_graph(..., tool_audit=…)`로 조립한다. 실제 서비스에 접속하지 않는다.

1. 경로별 완전성: 등록 2절 표의 9개 경로(읽기 성공·도구 오류·allowlist 거부·인자 오류·도구 예외·propose 혼합 호출·올바른 쓰기 제안·check 차단·마무리 단계)를 각각 실행하고, **모델이 낸 tool_call 수 = 그 경로의 `tool.call` 수 + `side_effect.draft`로 덮인 호출 수**이며 `status`가 표와 같다.
2. 한 응답에 읽기 호출 3개 → `tool.call` 3개, `tool_call_sha256`이 각 호출 ID의 sha256과 같다.
3. 레코드와 감사 파일 전체에 원문 인자 문자열·키 hex·도구 결과 본문이 없다.
4. 기록 실패(기록기의 `record`가 예외): 도구 실행 결과·응답 메시지·최종 상태가 기록기 없는 실행과 같고, trace에 `tool.audit_gap=True`가 있다(`test_agent_model_call_event.py`의 trace 확인 방식).
5. `tool_audit=None`이면 `tool.call`이 0개이고 나머지 감사 레코드는 기록기 있는 실행과 같다(`tool.call`만 다르다).
6. 기록이 이벤트 루프를 막지 않는다(`test_append_does_not_block_event_loop`와 같은 방식).
7. `govops.audit.log.TOOL_CALL_NAMES - {"unknown"}`이 모든 `PROFILES`의 도구 정의 이름 합집합과 같다(빈 집합이면 실패).
`tests/test_m7_freeze.py`: `EXPECTED["src/govops/agent"]` 값만 새 digest로 바꾼다(`uv run python scripts/freeze_digest.py` 출력의 해당 키). 다른 키는 그대로다.

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
   - 이 step이 허용한 동결 키 외의 동결 경로(프롬프트·도구·Guardrail·평가 러너·골든·mock·시드·`costs.py`·`infra/litellm/config.yaml`)를 바꾸지 않았는가?
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

- `graph.py`의 기존 동작(프롬프트·도구 설명·라우팅·`check`·승인·실행·`model.call` 기록·Guardrail)을 바꾸지 마라. 기록 지점 3곳과 `tool_audit` 인자만 더한다. 이유: 공식 구성 불변이며 골든·재생 테스트(`tests/test_golden_m3.py`·`tests/test_regolden.py`·`tests/test_golden_trace.py`)가 수정 없이 통과해야 한다(등록 7절).
- `tests/test_agent_graph.py`·`tests/test_agent_semantic.py`·`tests/test_agent_model_call_event.py`를 수정하지 마라. 이유: `tool_audit=None` 기본값에서 기존 정확 일치 단언이 그대로 통과하는 것이 이 설계의 증거다.
- `tool.call` 기록 실패를 fail-closed로 만들지 마라(`status="failed"` 등). 이유: 읽기 기록은 fail-open으로 등록했다(등록 6절). 부작용 의도 기록(fail-closed)과 다르다.
- `tests/test_m7_freeze.py`에서 `"src/govops/agent"` 외의 키를 바꾸지 마라. 이유: 이 step의 동결 변경은 `graph.py` 하나다(FP-071).
- 기록기·키 객체를 state·checkpoint·trace 속성·메시지에 넣지 마라. 이유: 키는 요청 헤더·감사 계층 밖으로 나가지 않는다(AGENTS CRITICAL).
