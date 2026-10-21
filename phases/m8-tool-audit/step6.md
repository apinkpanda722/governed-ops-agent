# Step 6: toolcall-check

## 읽어야 할 파일

먼저 아래 파일들을 읽고 프로젝트의 아키텍처와 설계 의도를 파악하라:

- `/AGENTS.md`(계층 경계: `audit/`는 `agent`·`tools`·`broker`·`api` import 금지, `## 명령어` 절 형식)
- `/docs/results/hypotheses-m8-tool-audit.md`(8절 대조 규칙과 CLI 출력, 10·12절, 읽기만 한다)
- `/src/govops/audit/verify.py`(CLI 형식과 안전한 출력의 선례, 읽기만 한다), `/src/govops/audit/records.py`(`_chain_files`·`_complete`로 체인을 읽는 방식)
- `/src/govops/obs/jsonl.py`(trace 파일 `<trace_dir>/<trace_id>.jsonl`, `GOVOPS_TRACE_DIR` 기본값), `/src/govops/obs/trace.py`(`SpanKind.TOOL_CALL = "tool_call"`, `trace(task_id)`: trace ID = 턴 ID)
- `/src/govops/agent/graph.py`의 `read_tools_body`(도구 이름 span, 읽기만 한다)
- `/Makefile`(`audit-verify` 266~269행), `/tests/test_makefile.py`(`audit-verify` 단언 320행~, `verify` 레시피 정확 일치)
- `/.claude/skills/failure-patterns/SKILL.md` — 읽기만 한다

## 작업

**수정할 파일**(이 목록이 전부다): `src/govops/audit/toolcall_check.py`(신규), `tests/test_audit_toolcall_check.py`(신규), `Makefile`(타깃 1개 추가에 한함), `tests/test_makefile.py`(새 테스트 함수 추가에 한함), `AGENTS.md`(`## 명령어` 절에 줄 1개 추가에 한함). TDD.

열람 금지 경로: `data/interim/`, `data/eval/`, `data/redteam/`, `src/govops/guardrails/rules_v1.json`, `.env`
수정 허용 경로: `src/govops/audit/toolcall_check.py`, `tests/test_audit_toolcall_check.py`, `Makefile`, `tests/test_makefile.py`, `AGENTS.md`
열람 금지의 범위: 이 금지는 **Codex 세션이 해당 경로의 파일을 직접 열람·출력·인용하는 것**에 대한 것이다. 이 step의 코드가 런타임에 그 경로를 다루는 것(예: 기본 감사 디렉터리 `data/interim/audit`, 기본 감사 키 경로 `data/interim/audit_ed25519.pem`, `.env`에서 오는 환경 변수 `AUDIT_ARGS_HMAC_KEY`)은 대상이 아니다. 테스트가 `data/interim/`·`data/eval/`·`data/redteam/`의 실제 파일 **내용**을 읽는 것은 여전히 금지이며 테스트는 임시 디렉터리·`tests/conftest.py`의 임시 감사 키·합성 HMAC 키만 쓴다. 예외로 `data/results/`의 실행 디렉터리는 파일 이름과 JSON 키 이름을 확인하는 데 한해 열람할 수 있고, 응답 본문(`answer`·`effect_bodies`)이나 근거 본문을 출력·인용하지 않는다. 이 제한은 이 step이 새로 작성하는 테스트에 한한 것이며, 기존 `tests/test_m7_freeze.py`가 `scripts/freeze_digest.py`로 해시를 계산하며 동결 경로 파일을 바이트로 읽는 것은 `make verify`의 일부이므로 허용한다. Codex 세션이 그 파일 내용을 출력·인용하는 것은 여전히 금지다.

**목적**: 머지 후 사용자 실행 확인(등록 12절)에서, 실제 대화 실행의 `tool.call` 개수가 trace의 도구 이름 span 수와 같은지 읽기 전용으로 대조한다.

### 1. `python -m govops.audit.toolcall_check`

```python
def check(audit_dir: Path, trace_dir: Path, *, since: datetime) -> CheckResult: ...
def main(argv: list[str] | None = None) -> int: ...
```

- 인자: `--since <UTC ISO 8601>`(선택, 기본 = 지금부터 60분 전), `--audit-dir`(기본 `env_or("AUDIT_DIR", "data/interim/audit")`), `--trace-dir`(기본 `env_or("GOVOPS_TRACE_DIR", <obs/jsonl.py의 기본값>)`). 인자 없는 `make audit-toolcall-check`가 동작해야 한다(등록 12절의 명령).
- 대상 턴: 체인의 `tool.call` 레코드 중 `ts >= since`인 것의 `task_id` 집합. 턴마다 `status ∈ {ok, tool_error, tool_not_available, invalid_argument, tool_unavailable}`인 `tool.call` 수와, `<trace_dir>/<task_id>.jsonl`에서 `kind == "tool_call"`이고 `name != "tool_attempt"`인 span 수를 비교한다(등록 8절). `proposal_invalid`·`not_executed`는 세지 않는다.
- 출력 한 줄(등록 8절 그대로): 모두 같으면 `PASS audit_toolcall_check turns=<n> tool_calls=<n> spans=<n>`, 아니면 `FAIL audit_toolcall_check <코드>`. 코드: 대상 턴 0개 `no_turns`(FP-069: 빈 집합을 PASS로 두지 않음), trace 파일 없음 `trace_missing`, 개수 다름 `count_mismatch`, 감사 디렉터리를 읽을 수 없거나 레코드가 JSON이 아님 `audit_unavailable`. 실패면 종료 코드 1. 여러 실패가 있으면 위 순서의 첫 코드.
- 읽기 전용이다. 경로·턴 ID·도구 이름·인자·해시·예외 문장을 출력하지 않는다(개수와 코드만). `govops.agent`·`api`·`tools`·`broker`를 import하지 않는다.

### 2. `Makefile`·`AGENTS.md`

`audit-verify` 타깃 뒤에 같은 형식으로:

```make
.PHONY: audit-toolcall-check
# 읽기 전용, 최근 실행의 tool.call 개수와 trace 도구 span 수 대조(M8 추가), 사용자 실행
audit-toolcall-check:
	uv run python -m govops.audit.toolcall_check $(TOOLCALL_CHECK_ARGS)
```

`AGENTS.md` `## 명령어`의 `make audit-verify` 줄 다음에 `make audit-toolcall-check # 최근 실행의 tool.call 개수와 trace 도구 span 수 대조(읽기 전용, 사용자 실행)` 한 줄. `CRITICAL:` 문장 수는 바꾸지 않는다.

### 3. 테스트

`tests/test_audit_toolcall_check.py`(임시 디렉터리, 합성 키의 `ToolCallAuditor`로 쓴 실제 체인과 합성 trace JSONL): 개수 일치 → PASS 줄 정확, 한 턴 span 하나 부족 → `count_mismatch`, trace 파일 없음 → `trace_missing`, `since` 이후 레코드 없음 → `no_turns`, 깨진 체인 줄 → `audit_unavailable`, `tool_attempt` span과 `proposal_invalid`·`not_executed` 레코드는 세지 않음, 출력에 임시 경로·턴 ID·키 hex가 없음, 등록 문서 8절의 코드 4개와 PASS 줄 형식이 코드와 같음(문서에서 뽑은 집합이 비어 있으면 실패), 등록 12절의 `make audit-toolcall-check` 예시가 `Makefile` 타깃과 같음.
`tests/test_makefile.py`: 새 함수 하나 — 타깃이 `.PHONY`에 있고 `verify` 레시피에 없으며 레시피가 위와 같고 `AGENTS.md` 줄에 `(사용자 실행)`이 있다. 기존 단언은 바꾸지 않는다.

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

- `make audit-toolcall-check`와 `make audit-verify`를 실행하지 마라. 이유: 기본 경로가 실제 감사 디렉터리(`data/interim/audit`)와 trace 디렉터리다. 테스트는 `main(argv)`·`check()`를 임시 디렉터리로 직접 부른다.
- 대상이 0개일 때 PASS를 내지 마라. 이유: 공허 통과다(FP-069). `no_turns`로 실패한다.
- 등록 8절에 없는 출력 형식·코드를 만들지 마라. 이유: 등록 문서가 단일 출처다(FP-043).
- 체인이나 trace 파일에 쓰지 마라. 이유: 읽기 전용 점검이다.
