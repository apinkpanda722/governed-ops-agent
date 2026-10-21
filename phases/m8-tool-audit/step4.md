# Step 4: records-tool-view

## 읽어야 할 파일

먼저 아래 파일들을 읽고 프로젝트의 아키텍처와 설계 의도를 파악하라:

- `/AGENTS.md`(계층 경계: `audit/`), `/docs/ADR.md`(ADR-028: 기록 화면은 허용 목록 투영, 해시 비노출)
- `/docs/results/hypotheses-m8-tool-audit.md`(9절 화면 노출, 읽기만 한다), `/docs/M8_TOOL_AUDIT_DRAFT.md`(8절)
- `/src/govops/audit/records.py`(`ALLOWED_EVENT_FIELDS` 22~33행, `_ENUMS` 37~43행, `_display_field` 64~70행, `_event` 73~109행, `read_events` 112행~)
- `/src/govops/audit/log.py`(step 1의 `TOOL_CALL_NAMES`·`TOOL_CALL_STATUSES`)
- `/tests/test_audit_records.py`(`ALLOWED` 24행~과 75행의 정확 일치 단언, 158행의 부분집합 단언), `/tests/test_api_audit_view.py`(`OPTIONAL` 36행~과 258행의 부분집합 단언)
- `/.claude/skills/failure-patterns/SKILL.md` — 읽기만 한다

## 작업

**수정할 파일**(이 목록이 전부다): `src/govops/audit/records.py`(`tool.call` 표시 필드 추가에 한함), `tests/test_audit_records.py`(`ALLOWED` 집합에 세 항목 추가와 새 테스트 함수 추가에 한함), `tests/test_api_audit_view.py`(`OPTIONAL` 집합에 세 항목 추가와 새 테스트 함수 추가에 한함). TDD.

열람 금지 경로: `data/interim/`, `data/eval/`, `data/redteam/`, `src/govops/guardrails/rules_v1.json`, `.env`
수정 허용 경로: `src/govops/audit/records.py`, `tests/test_audit_records.py`, `tests/test_api_audit_view.py`
열람 금지의 범위: 이 금지는 **Codex 세션이 해당 경로의 파일을 직접 열람·출력·인용하는 것**에 대한 것이다. 이 step의 코드가 런타임에 그 경로를 다루는 것(예: 기본 감사 디렉터리 `data/interim/audit`, 기본 감사 키 경로 `data/interim/audit_ed25519.pem`, `.env`에서 오는 환경 변수 `AUDIT_ARGS_HMAC_KEY`)은 대상이 아니다. 테스트가 `data/interim/`·`data/eval/`·`data/redteam/`의 실제 파일 **내용**을 읽는 것은 여전히 금지이며 테스트는 임시 디렉터리·`tests/conftest.py`의 임시 감사 키·합성 HMAC 키만 쓴다. 예외로 `data/results/`의 실행 디렉터리는 파일 이름과 JSON 키 이름을 확인하는 데 한해 열람할 수 있고, 응답 본문(`answer`·`effect_bodies`)이나 근거 본문을 출력·인용하지 않는다. 이 제한은 이 step이 새로 작성하는 테스트에 한한 것이며, 기존 `tests/test_m7_freeze.py`가 `scripts/freeze_digest.py`로 해시를 계산하며 동결 경로 파일을 바이트로 읽는 것은 `make verify`의 일부이므로 허용한다. Codex 세션이 그 파일 내용을 출력·인용하는 것은 여전히 금지다.

**목적**: 소유자 기록 화면의 서버 투영이 `tool.call`의 도구 이름·상태·결과 개수를 보여 주고 해시는 보여 주지 않게 한다. 웹(BFF·화면)은 다음 step이다.

### 1. `audit/records.py`

- `ALLOWED_EVENT_FIELDS`에 `"tool_name"`·`"status"`·`"result_count"`를 더한다. `_ENUMS`에 `"tool_name": log.TOOL_CALL_NAMES`, `"status": log.TOOL_CALL_STATUSES`.
- `_display_field`: `result_count`는 `type(value) is int`이고 0~10000일 때만 표시한다(`bool` 거부). 기존 필드 규칙은 바꾸지 않는다.
- `args_hmac`·`args_hmac_kid`·`tool_call_sha256`은 허용 목록에 넣지 않는다(해시 비노출, ADR-028). `read_events`의 `limit=500`은 바꾸지 않는다(등록 11절 한계).

### 2. 테스트

`tests/test_audit_records.py`: `ALLOWED`에 세 항목만 더한다(75행 정확 일치 유지). 새 테스트: 실제 `AuditLog`로 쓴 `tool.call`(합성 키의 `ToolCallAuditor`)의 투영이 정확히 `{seq, ts, event, actor, hash12, tool_name, status, result_count}`이고 해시 세 필드가 없음, enum 밖 값·`bool`·범위 밖 `result_count`는 표시하지 않음, `tool.call` 600개가 있는 턴에서 `truncated=True`(상한 500 유지).
`tests/test_api_audit_view.py`: `OPTIONAL`에 세 항목만 더한다(258행 부분집합 단언 유지). 새 테스트: 소유자 기록 화면 API 응답에 `tool.call` 이벤트가 위 필드로 보이고 해시 세 필드와 키 hex가 응답 본문에 없음.

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

- 해시 필드(`args_hmac`·`args_hmac_kid`·`tool_call_sha256`)나 그 일부를 화면 투영에 넣지 마라. 이유: ADR-028과 등록 9절의 해시 비노출 원칙이다.
- `tests/test_audit_records.py`의 75행 정확 일치 단언을 부분집합·포함 비교로 바꾸지 마라. 이유: 정확 일치는 세 항목 추가에 한한다(FP-071).
- `read_events`의 `limit` 기본값을 바꾸지 마라. 이유: 표시 상한은 등록 한계로 남긴다(등록 11절). 바꾸면 기존 잘림 테스트의 의미가 달라진다.
- `src/govops/audit/log.py`·`verify.py`를 수정하지 마라. 이유: 동결 파일이며 이 step의 수정 파일이 아니다.
