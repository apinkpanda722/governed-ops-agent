# Step 1: audit-tool-event

## 읽어야 할 파일

먼저 아래 파일들을 읽고 프로젝트의 아키텍처와 설계 의도를 파악하라:

- `/AGENTS.md`(계층 경계: `audit/`는 `agent`·`tools`·`broker`·`api` import 금지. CRITICAL: 감사 로그·토큰 규칙)
- `/docs/results/hypotheses-m8-tool-audit.md`(step 0 등록. 이름·값의 단일 출처, 읽기만 한다)
- `/docs/M8_TOOL_AUDIT_DRAFT.md`(2·5절)
- `/src/govops/audit/log.py`(`EVENTS` 20~42행, `_GUARDRAIL_FORMATS` 98~104행, `_NEW_REQUIRED` 154행~, `_NEW_FIELDS` 180~183행, `_NEW_FORMATS`·`_NEW_ENUMS`, `_validate_new` 212행~, `_validate` 253행~)
- `/src/govops/audit/crypto.py`(`AuditKeyError` 39~46행, `load_signing_key` 90~121행. 읽기만 한다, 동결)
- `/tests/test_audit.py`(`test_events` 67행), `/tests/test_audit_events.py`(`CASES`, `test_new_fields_are_event_scoped` 250행~), `/tests/test_m7_freeze.py`
- `/.claude/skills/failure-patterns/SKILL.md` — 읽기만 한다

## 작업

**수정할 파일**(이 목록이 전부다): `src/govops/audit/log.py`(`tool.call` 이벤트와 필드 형식 추가에 한함), `src/govops/audit/argmac.py`(신규), `src/govops/audit/toolcall.py`(신규), `tests/test_audit_tool_call.py`(신규), `tests/test_audit.py`(`test_events`의 기대 집합에 `"tool.call"` 한 항목 추가, 같은 테스트 안에서 `tool.call`에만 유효한 합성 필수 필드를 넘기도록 한 줄 보완(지역 상수 `TOOL_CALL_FIELDS`와 `CASES.get(event, TOOL_CALL_FIELDS if event == "tool.call" else {})`), 그리고 `tool.call` 레코드가 감사 파일에 정확히 1줄 기록됐다는 단언 한 줄에 한함. 그 밖의 단언 불변), `tests/test_m7_freeze.py`(`EXPECTED`의 `"src/govops/audit/log.py"` 값 한 줄 갱신에 한함). TDD.

열람 금지 경로: `data/interim/`, `data/eval/`, `data/redteam/`, `src/govops/guardrails/rules_v1.json`, `.env`
수정 허용 경로: `src/govops/audit/log.py`, `src/govops/audit/argmac.py`, `src/govops/audit/toolcall.py`, `tests/test_audit_tool_call.py`, `tests/test_audit.py`, `tests/test_m7_freeze.py`
열람 금지의 범위: 이 금지는 **Codex 세션이 해당 경로의 파일을 직접 열람·출력·인용하는 것**에 대한 것이다. 이 step의 코드가 런타임에 그 경로를 다루는 것(예: 기본 감사 디렉터리 `data/interim/audit`, 기본 감사 키 경로 `data/interim/audit_ed25519.pem`, `.env`에서 오는 환경 변수 `AUDIT_ARGS_HMAC_KEY`)은 대상이 아니다. 테스트가 `data/interim/`·`data/eval/`·`data/redteam/`의 실제 파일 **내용**을 읽는 것은 여전히 금지이며 테스트는 임시 디렉터리·`tests/conftest.py`의 임시 감사 키·합성 HMAC 키만 쓴다. 예외로 `data/results/`의 실행 디렉터리는 파일 이름과 JSON 키 이름을 확인하는 데 한해 열람할 수 있고, 응답 본문(`answer`·`effect_bodies`)이나 근거 본문을 출력·인용하지 않는다. 이 제한은 이 step이 새로 작성하는 테스트에 한한 것이며, 기존 `tests/test_m7_freeze.py`가 `scripts/freeze_digest.py`로 해시를 계산하며 동결 경로 파일을 바이트로 읽는 것은 `make verify`의 일부이므로 허용한다. Codex 세션이 그 파일 내용을 출력·인용하는 것은 여전히 금지다.

**목적**: 감사 계층에 `tool.call` 이벤트 형식, 인자 HMAC 키 로더, 기록기를 만든다. 그래프·조립은 다음 step이다.

### 1. `src/govops/audit/log.py` (추가에 한함)

- `EVENTS`에 `"tool.call"`을 더한다(`SYSTEM_EVENTS` 아님: `task_id`·`actor_sub` 형식 검사를 받는다).
- 상수: `TOOL_CALL_NAMES`(등록 3절의 10개, `unknown` 포함), `TOOL_CALL_STATUSES`(7개).
- `_NEW_REQUIRED["tool.call"] = {"tool_name", "status", "result_count", "tool_call_sha256", "args_hmac", "args_hmac_kid"}`. 형식: `tool_call_sha256`·`args_hmac` → `_HASH`, `args_hmac_kid` → `[0-9a-f]{12}`, `tool_name` → `_NEW_ENUMS`(`TOOL_CALL_NAMES`), `status` → `_NEW_ENUMS`(`TOOL_CALL_STATUSES`), `result_count` → 정수 0~10000(`_validate_new`의 정수 분기에 상한 추가).
- **`_NEW_FIELDS`에서 `tool_call_sha256`을 뺀다**(`team`·`reason`과 같은 처리). 이유: 옛 이벤트 `guardrail.tool_quarantined`가 이 필드를 쓰고(`_GUARDRAIL_FORMATS`), `_NEW_FIELDS`는 옛 이벤트에서 금지되기 때문이다. 이 처리 없이 시뮬레이션하면 228개 테스트가 `audit_write_failed`로 실패했다(초안 4절). 새 이벤트에 `args_sha256`을 쓰지 않는다(옛 이벤트 `side_effect.attempt`·`result`가 쓴다).
- 그 밖의 기존 이벤트·필드·검증 규칙은 바꾸지 않는다. `verify.py`는 `_validate_event`를 그대로 쓰므로 바꾸지 않는다.

### 2. `src/govops/audit/argmac.py`

```python
ENV_NAME = "AUDIT_ARGS_HMAC_KEY"
class ArgsMacKeyError(Exception):          # code ∈ {"audit_args_hmac_key_missing", "audit_args_hmac_key_invalid", "audit_args_hmac_key_reused"}
    def __init__(self, code: str): ...
@dataclass(frozen=True)
class ArgsMac:
    kid: str                                 # repr에 키를 넣지 않는다
    def digest(self, tool_name: str, arguments: object) -> str: ...   # 64 hex
def load_args_mac(*, signing_key_raw: bytes | None = None) -> ArgsMac: ...
```

- 키는 `os.environ.get(ENV_NAME, "")`(비어 있지 않은 기본값 금지, `tests/test_env_defaults.py` 스캐너)에서 읽는다. 공백만·없음 → `audit_args_hmac_key_missing`, `^[0-9a-f]{64}$`가 아님 → `audit_args_hmac_key_invalid`, `signing_key_raw`가 주어졌고 키 바이트와 같음 → `audit_args_hmac_key_reused`. 예외 메시지·repr·로그에 키 값을 넣지 않는다.
- `kid`·정규화 입력·HMAC은 등록 4절 그대로(`hmac.new(key, data, hashlib.sha256).hexdigest()`). `digest`의 `arguments`는 모델이 낸 원래 인자 값(보통 문자열)이다.
- `ArgsMac`은 키 바이트를 비공개 속성으로만 가진다. `__repr__`·`__eq__`에 키가 나오지 않게 한다(`dataclass(repr=False)` 또는 `field(repr=False)`).

### 3. `src/govops/audit/toolcall.py`

```python
class ToolCallAuditor:
    def __init__(self, audit: AuditPort, mac: ArgsMac): ...
    def record(self, *, task_id: str, actor_sub: str, tool_name: object, tool_call_id: object, arguments: object, status: str, result_count: int = 0) -> int: ...
```

- `tool_name`이 `TOOL_CALL_NAMES - {"unknown"}`에 있으면 그대로, 아니면 `"unknown"`으로 기록한다(모델이 지어낸 문자열을 남기지 않는다). HMAC 입력에는 원래 이름을 쓴다.
- `tool_call_sha256 = sha256(str(tool_call_id).encode("utf-8")).hexdigest()`(`guardrail.tool_quarantined`와 같은 방식). `tool_call_id`가 문자열이 아니면 `""`로 본다.
- `audit.append("tool.call", task_id=…, actor_sub=…, tool_name=…, status=…, result_count=…, tool_call_sha256=…, args_hmac=mac.digest(…), args_hmac_kid=mac.kid)`를 부르고 그 seq를 돌려준다. 예외는 그대로 올린다(fail-open 처리는 호출하는 그래프가 한다).

### 4. 테스트

`tests/test_audit_tool_call.py`: 정상 기록(필드 6개 정확, 키 값·원문 인자 문자열이 레코드에 없음), `verify_chain` 통과, 필드 누락·추가·형식 오류(대문자 hex, 13자 kid, `result_count` -1·10001·bool, enum 밖 `status`·`tool_name`) 거부, `tool_call_sha256`을 `guardrail.tool_quarantined`가 계속 쓸 수 있음(회귀), `args_hmac`·`args_hmac_kid`·`tool_name`·`status`·`result_count`가 다른 이벤트에서 거부됨, 키 로더 오류 3종과 공백 값, `kid`·HMAC이 등록 식으로 독립 계산한 값과 같음(JSON dict·순서만 다른 dict는 같은 값·비JSON 문자열·비문자열), `ArgsMac`의 `repr`과 예외 문자열에 키 hex가 없음, 알려진 이름은 그대로·모르는 이름은 `unknown`, 시드 고정(`random.Random(20261021)`) 무작위 tool_call ID 10,000개의 해시가 모두 기록됨(FP-010: 형식 검증만, PII 검사에 걸리지 않음), **등록 문서 3·10절의 enum·코드 목록과 상수가 같음**(문서에서 뽑은 집합이 비어 있으면 실패, FP-069). 합성 키는 `secrets.token_hex(32)`로 테스트 안에서 만들고 `monkeypatch.setenv`로 넣는다.
`tests/test_audit.py::test_events`: 기대 집합에 `"tool.call"` 한 항목을 더하고, 기록 루프가 `tool.call`에만 지역 상수 `TOOL_CALL_FIELDS`(등록 3절 형식의 합성 값: `tool_name` `"search_docs"`, `status` `"ok"`, `result_count` 0, `tool_call_sha256`·`args_hmac` 64 hex, `args_hmac_kid` 12 hex)를 넘기도록 `CASES.get(event, TOOL_CALL_FIELDS if event == "tool.call" else {})`로 바꾼다. 같은 테스트에 `tool.call` 레코드가 감사 파일에 정확히 1줄 있다는 단언을 더한다(공허 방지, FP-069). **`tests/test_audit_events.py`의 `CASES`에 `tool.call`을 넣지 않는다**: `emit`·`test_identity`가 `CASES`의 이벤트를(`model.call`·`conversation.deleted` 제외) 시스템 이벤트로 다루고, `test_new_fields_are_event_scoped`가 `CASES`의 각 키를 다른 모든 이벤트에서 거부하라고 단언하는데 `tool_call_sha256`은 `guardrail.tool_quarantined`가 허용하기 때문이다. `tool.call`의 형식·거부 테스트는 새 파일 `tests/test_audit_tool_call.py`에 둔다(위 목록).
`tests/test_m7_freeze.py`: `EXPECTED["src/govops/audit/log.py"]` 값만 새 digest로 바꾼다(`uv run python scripts/freeze_digest.py` 출력의 해당 키, 이 명령은 읽기 전용이다). 다른 키는 그대로이며 `test_digests_match_expected`가 통과해야 한다.

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

- `src/govops/audit/crypto.py`·`verify.py`·`smoke.py`·`anchor.py`·`__init__.py`를 수정하지 마라. 이유: 동결 파일이고 이 step의 동결 변경은 `audit/log.py` 하나다(등록 7절). `AuditKeyError`의 코드 목록을 넓히지 않고 `ArgsMacKeyError`를 따로 둔다.
- `tests/test_m7_freeze.py`에서 `"src/govops/audit/log.py"` 외의 키를 바꾸지 마라. 이유: 다른 동결 경로 불변이 공식 구성 불변의 증거다(FP-071: 정확 일치 단언은 해당 키에 한함).
- 키 값·원문 인자·HMAC 입력 문자열을 예외·repr·로그·테스트 출력에 남기지 마라. 이유: 토큰·키 값 노출 금지(AGENTS CRITICAL), 원문 인자 금지(ADR-012).
- 해시·kid·tool_call ID에 PII 정규식을 돌리지 마라. 형식 검증만 한다. 이유: 무작위 hex가 PII 규칙에 걸린다(FP-010).
- 기존 테스트 `tests/test_audit_events.py`를 수정하지 마라. 수정 없이 통과해야 한다. 이유: 기존 이벤트 계약 불변의 증거다.
