# Step 0: tool-audit-registration

## 읽어야 할 파일

먼저 아래 파일들을 읽고 프로젝트의 아키텍처와 설계 의도를 파악하라:

- `/AGENTS.md`(CRITICAL: 감사 로그·토큰·.env 규칙)
- `/docs/M8_TOOL_AUDIT_DRAFT.md`(전체. 이 step이 등록하는 내용의 출처)
- `/docs/M8_DESIGN_DRAFT.md`(1-3절 digest 갱신 절차)
- `/docs/ADR.md`(ADR-012, ADR-015, ADR-018, ADR-028의 형식)
- `/docs/results/hypotheses-m8-rollout-addendum-1.md`(사전 등록 문서 형식 선례, 읽기만 한다)
- `/tests/test_m8_freeze.py`, `/tests/test_m7_freeze.py`(동결 방식, 읽기만 한다)
- `/tests/test_docs_consistency.py`(ADR 관련 단언: 505~512행의 ADR-001~024 본문 해시, ADR별 marker 단언)
- `/.claude/skills/failure-patterns/SKILL.md` — 읽기만 한다

## 작업

**수정할 파일**(이 목록이 전부다): `docs/results/hypotheses-m8-tool-audit.md`(신규), `docs/ADR.md`(파일 끝에 ADR-029 한 절 추가에 한함), `tests/test_m8_freeze.py`(`EXPECTED`에 키 하나 추가에 한함).

열람 금지 경로: `data/interim/`, `data/eval/`, `data/redteam/`, `src/govops/guardrails/rules_v1.json`
수정 허용 경로: `docs/results/hypotheses-m8-tool-audit.md`, `docs/ADR.md`, `tests/test_m8_freeze.py`
열람 금지의 범위: 이 금지는 **Codex 세션이 해당 경로의 파일을 직접 열람·출력·인용하는 것**에 대한 것이다. 이 step의 코드가 런타임에 그 경로를 다루는 것(예: 기본 감사 디렉터리 `data/interim/audit`, 기본 감사 키 경로 `data/interim/audit_ed25519.pem`, `.env`에서 오는 환경 변수 `AUDIT_ARGS_HMAC_KEY`)은 대상이 아니다. 테스트가 `data/interim/`·`data/eval/`·`data/redteam/`의 실제 파일 **내용**을 읽는 것은 여전히 금지이며 테스트는 임시 디렉터리·`tests/conftest.py`의 임시 감사 키·합성 HMAC 키만 쓴다. 예외로 `data/results/`의 실행 디렉터리는 파일 이름과 JSON 키 이름을 확인하는 데 한해 열람할 수 있고, 응답 본문(`answer`·`effect_bodies`)이나 근거 본문을 출력·인용하지 않는다. 이 제한은 이 step이 새로 작성하는 테스트에 한한 것이며, 기존 `tests/test_m7_freeze.py`가 `scripts/freeze_digest.py`로 해시를 계산하며 동결 경로 파일을 바이트로 읽는 것은 `make verify`의 일부이므로 허용한다. Codex 세션이 그 파일 내용을 출력·인용하는 것은 여전히 금지다.

**목적**: 감사 체인에 모든 tool_call을 남기는 변경의 범위·형식·해시·실패 처리·동결 경계를 **코드보다 먼저** 고정한다(FP-043). 이 step은 코드를 만들지 않는다. 이후 step 1~6이 이 문서의 이름·값을 그대로 구현하고, 문서와 코드의 일치를 테스트로 검사한다.

### 1. 등록 문서 `docs/results/hypotheses-m8-tool-audit.md`

형식은 `hypotheses-m8-rollout-addendum-1.md`를 따른다. 첫 줄 아래에 `M8 추가 — 공식 M6 수치 아님`과 `scope: "m8"`을 쓴다. 아래 절을 이 순서로 쓰고, 값은 `docs/M8_TOOL_AUDIT_DRAFT.md`와 아래에 적은 그대로 옮긴다(바꾸거나 더하지 않는다).

1. **범위와 사용자 결정**: 초안 0·1절(결정 7개, 2026-10-21).
2. **완전성 원칙과 경로 대응 표**: 초안 3절 표 전체(경로 9행, 지금 감사, 결정). 올바른 쓰기 제안과 `check` 차단은 기존 `side_effect.draft`가 덮는다는 문장 포함. 완전성은 `build_task_service`로 조립한 서비스에서 보장한다.
3. **레코드 형식**: 이벤트 `tool.call`. 필드 표(초안 5절): `tool_name`(enum 10개: `get_document`·`jira_add_comment`·`jira_create_followup_ticket`·`jira_get_ticket`·`jira_search_tickets`·`lookup_id`·`run_sql`·`search_docs`·`slack_send_message`·`unknown`), `status`(enum 7개: `ok`·`tool_error`·`tool_not_available`·`invalid_argument`·`tool_unavailable`·`proposal_invalid`·`not_executed`), `result_count`(0~10000 정수), `tool_call_sha256`(64 hex), `args_hmac`(64 hex), `args_hmac_kid`(12 hex). 원문 인자·결과 본문 금지. `_NEW_FIELDS`에서 `tool_call_sha256`을 빼는 이유(옛 이벤트 `guardrail.tool_quarantined`가 쓴다)와 `args_sha256`을 새 이벤트에 쓰지 않는 이유(옛 이벤트 `side_effect.attempt`·`result`가 쓴다)를 적는다.
4. **해시 방식**: HMAC-SHA256. 정규화 입력: 인자가 JSON dict이면 `json.dumps({"tool": <원래 이름>, "args": <dict>}, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)`, 아니면 문자열 인자는 `"raw\0" + <원래 이름> + "\0" + <원문>`, 문자열이 아닌 인자는 `"raw-nonstr\0" + <원래 이름>`(모두 UTF-8). 키 `AUDIT_ARGS_HMAC_KEY`(64자리 hex, 32바이트), `args_hmac_kid = sha256(b"govops-args-hmac\0" + key_bytes).hexdigest()[:12]`. 무염 sha256·HMAC 비교와 결정 근거(초안 1절 끝 문단, 사용자 결정 2~7). 읽기는 HMAC, 쓰기는 기존 무염 `draft_sha256`·`args_sha256` 유지(승인 결합·동결).
5. **키 검사와 시작 거부**: `audit_args_hmac_key_missing`·`audit_args_hmac_key_invalid`·`audit_args_hmac_key_reused`(서명 키 raw 32바이트와 같음). `audit/argmac.py`의 `ArgsMacKeyError`를 `build_task_service`가 `StartupError`로 바꾼다(`AuditKeyError`의 코드 목록은 동결 파일에 고정).
6. **실패 처리**: fail-open, trace 속성 `tool.audit_gap=True`(초안 6절과 그 이유).
7. **동결 경계**: 초안 7절 표(키 `src/govops/agent` 이전 digest `f801700264f23227445cc0dd1d248da52a2af9708b004ba2341e9bc598c9d58e`, 키 `src/govops/audit/log.py` 이전 digest `014309c3b447ef2e34af338b53e39f1ef77d99fd5945cef03ea070f12acbf22d`, 사유). **새 digest는 변경 뒤에만 알 수 있으므로 이 문서에는 적지 않는다. 코드를 바꾸는 step이 `tests/test_m7_freeze.py`의 `EXPECTED` 해당 키만 갱신하고, 결과 문서의 "digest 변경 이력" 표에 새 digest를 적는다.** 다른 키는 그대로임을 `test_digests_match_expected`가 확인한다. 공식 구성 불변 증거(초안 7절: `HOOK_FILES`가 `graph.py`·`audit/log.py`를 포함하지 않음, 골든·재생 테스트 무수정 통과).
8. **trace 대조 규칙**: 턴마다 `tool.call` 중 `status ∈ {ok, tool_error, tool_not_available, invalid_argument, tool_unavailable}`의 개수 = trace의 `kind == "tool_call"`이고 `name != "tool_attempt"`인 span 수. `proposal_invalid`·`not_executed`는 대조에서 뺀다. CLI `python -m govops.audit.toolcall_check`(`make audit-toolcall-check`) 출력은 `PASS audit_toolcall_check turns=<n> tool_calls=<n> spans=<n>` 또는 `FAIL audit_toolcall_check <코드>`(코드: `count_mismatch`·`no_turns`·`trace_missing`·`audit_unavailable`).
9. **화면 노출**: 초안 8절.
10. **고정 코드 목록**(단일 출처): 이벤트 `tool.call`; 위 `status` 7개; 키 오류 3개; trace 속성 `tool.audit_gap`; 대조 CLI 코드 4개.
11. **한계**: 초안 11절 전체.
12. **사용자 실행**(블록당 한 명령): 초안 10절의 다섯 명령. 키 생성 명령은 값을 화면에 출력하지 않고 `.env`에 덧붙이는 형태 그대로 쓴다.

### 2. `docs/ADR.md` — ADR-029 추가

파일 끝(ADR-028 뒤)에 `### ADR-029: M8 결정 — 감사 체인에 모든 도구 호출 기록` 절을 ADR-028과 같은 형식(`**상태**: 유효`, `**날짜**: 2026-10-21`, `**결정**:`, `**이유**:`, `**트레이드오프**:`, `**변경 기록**:`)으로 더한다. 결정: ADR-012의 감사 범위를 읽기 도구 호출까지 넓힘(`tool.call`), 읽기 인자는 전용 키 HMAC(감사 서명 키와 분리)·쓰기는 기존 무염 해시 유지와 그 이유, 키 없으면 시작 거부, 기록 실패는 fail-open(ADR-012의 fail-closed는 부작용 의도 기록에 한정되는 이유), 데이터량 영향(호출당 서명 1회, 합성 측정 중앙값 0.15 ms·p95 0.22 ms). 변경 기록에 "ADR-001~028과 기존 사전 등록 파일은 보존한다"를 쓴다. **ADR-001~028 본문은 한 글자도 바꾸지 않는다**(`tests/test_docs_consistency.py` 505~512행이 ADR-001~024 본문을 해시로 고정한다).

### 3. `tests/test_m8_freeze.py`

`EXPECTED`에 `"docs/results/hypotheses-m8-tool-audit.md": "<digest>"` 한 키를 추가한다. digest는 이 파일의 `single_file_digest`로 1번의 **최종** 본문에 대해 계산한 값이다. 다른 키와 함수는 바꾸지 않는다.

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

- 등록 값(enum 목록, 코드 이름, 키 이름·형식, kid 식, 정규화 식, 대조 규칙, CLI 출력)을 초안과 다르게 바꾸거나 새 값을 더하지 마라. 이유: 이 문서는 커밋 뒤 동결되며 이후 step이 값을 그대로 구현한다(FP-043).
- `docs/ADR.md`의 기존 ADR-001~028 본문·변경 기록을 고치지 마라. 이유: 문서 일관성 테스트가 본문 해시와 marker로 고정한다. ADR-012를 넓히는 내용은 ADR-029에만 쓴다.
- 등록 문서에 새 digest 값을 쓰지 마라. 이유: 변경 뒤에만 알 수 있고, 미리 쓰면 등록이 결과에 맞춰진다(FP-043). 코드 step의 `EXPECTED` 갱신과 결과 문서가 기록한다.
- `git add`·`git commit`을 하지 마라. 이유: 실행기가 step 완료 뒤 커밋한다. 동결 테스트는 `single_file_digest`(git 미사용)를 쓴다(FP-074).
