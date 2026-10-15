# Step 1: audit-records-reader

## 읽어야 할 파일

먼저 아래 파일들을 읽고 프로젝트의 아키텍처와 설계 의도를 파악하라:

- `/AGENTS.md`(계층 경계, CRITICAL 규칙)
- `/docs/ARCHITECTURE.md`, `/docs/ADR.md`, `/docs/THREAT_MODEL.md`
- `/docs/M7_DESIGN_DRAFT.md`(이 phase의 승인된 설계), `/docs/results/hypotheses-m7-records-authz.md`(사전 등록, 읽기만 한다)
- `/.claude/skills/failure-patterns/SKILL.md` — 읽기만 한다
- `/src/govops/audit/verify.py`(`verify_chain`, `read_anchor_lines`, `ChainReport`, `Problem`: 호출만 한다, 수정하지 않는다)
- `/src/govops/audit/log.py`(`_validate_event`, `EVENTS`, 형식 정규식)
- `/src/govops/audit/smoke.py`(체인 변조 합성 방식)
- `/tests/conftest.py`(`isolated_audit_environment`, `audit_key_pair`)
- `/tests/test_audit.py`, `/tests/test_architecture.py`

이전 step에서 만들어진 코드를 꼼꼼히 읽고, 설계 의도를 이해한 뒤 작업하라.

## 작업

**수정할 파일**(이 목록이 전부다): `src/govops/audit/records.py`, `tests/test_audit_records.py`. TDD.

열람 금지 경로: `data/interim/`
수정 허용 경로: `src/govops/audit/records.py`, `tests/test_audit_records.py`

열람 금지의 범위: 이 금지는 **Codex 세션이 해당 경로의 파일을 직접 열람·출력·인용하는 것**에 대한 것이다. 이 step의 코드와 테스트가 런타임에 그 경로를 다루는 것은 대상이 아니다. 단 테스트가 `data/interim/`의 실제 파일 **내용**을 읽는 것은 여전히 금지이며 테스트는 임시 디렉터리·합성 데이터·테스트용 키(`tests/conftest.py`의 `isolated_audit_environment`)만 쓴다. 기본 경로 문자열(예: `AUDIT_DIR`의 기본값)이 코드에 있는 것은 대상이 아니다.

**배경**: 기록 화면이 쓸 **읽기 전용** 모듈이다. 체인을 쓰거나 고치지 않는다. 검증 로직은 `verify_chain`을 호출만 하고 다시 구현하지 않는다(재구현하면 탐지 규칙이 둘이 된다).

### 1. 시그니처

```python
ALLOWED_EVENT_FIELDS: frozenset[str]   # tool, decision, check_result, reason, code, mock_id, fallback, cost_usd
class AuditUnavailable(Exception):     # .code == "audit_unavailable"
def read_events(directory: Path, task_ids: Collection[str], *, limit: int = 500) -> tuple[dict[str, list[dict]], bool]
def verify_summary(directory: Path, *, public_key_path: Path | None = None, anchor_paths: Sequence[Path] | None = None, repo_root: Path | None = None) -> dict
class VerifySummaryCache:              # ttl=10.0, clock 주입, 단일 비행
    def get(self, key, compute: Callable[[], dict]) -> dict
```

### 2. `read_events`

- `directory`의 `chain-*.jsonl`을 정렬 순서로 읽고 **마지막 개행까지만** 소비한다(쓰기 도중 미완성 줄 무시). 줄이 JSON이 아니거나 `log._validate_event`를 통과하지 못하는 레코드는 건너뛴다.
- `task_id`가 `task_ids`에 속한 레코드만 이벤트로 만든다. 이벤트 키는 `seq`·`ts`·`event`·`actor`(`actor.sub`)·`hash12`(`hash` 앞 12자)와 `ALLOWED_EVENT_FIELDS` 중 레코드에 있고 형식이 맞는 것, `draft_hash12`(`draft_hash`가 있으면 앞 12자)뿐이다. `sig`·`prev_hash`·`kid`·`hash` 전체·`input_sha256`·`tool_call_sha256`·`deployment_id`·`team`은 넣지 않는다.
- 값 형식 이중 방어: `mock_id`는 `message-N`·`comment-N`·`ABC-N`, `cost_usd`는 `\d+\.\d{6}`, `fallback`은 bool, `tool`·`decision`·`check_result`·`reason`·`code`는 `log.py`의 열거형 집합 안의 값만 통과한다. 통과하지 못하면 그 필드만 버린다.
- 반환의 둘째 값은 이벤트 수가 `limit`을 넘어 잘렸는지다(대화 전체 이벤트 합 기준, 잘리면 앞쪽부터 유지).
- `directory`가 없거나 읽을 수 없으면 `AuditUnavailable`.

### 3. `verify_summary`

- `public_key_path` 기본: `env_or("AUDIT_PUBLIC_KEY_FILE", "data/seed/audit_public_key.pem")`(저장소 루트 기준), 고정점 기본: `env_or("AUDIT_ANCHORS_PENDING_FILE", "data/interim/audit/anchors-pending.jsonl")`과 `docs/results/audit-anchors.jsonl`(`verify.py:main`과 같은 기본값). 경로는 저장소 루트(`verify.ROOT`) 기준으로 절대화한다(cwd 의존 금지).
- `load_public_keys`·`read_anchor_lines`가 `AuditKeyError`·`ValueError`를 던지면 `AuditUnavailable`.
- 마지막 체인 파일이 개행 없이 끝나면(쓰기 도중) 체인을 **개행까지의 스냅샷**으로 임시 디렉터리(`tempfile.TemporaryDirectory`)에 복사해 검증한다. 복사본 위치는 `repo_root` 아래 같은 상대 경로여야 고정점 `dir` 매칭이 성립한다(임시 `repo_root`를 만들고 같은 상대 경로로 `chain-*.jsonl`·legacy 파일을 복사). 완성된 파일만이면 실제 디렉터리를 그대로 검증한다.
- 반환 키는 정확히 `status`(`"pass"`|`"fail"`)·`checked_at`(UTC ISO)·`chain`(`records`·`files`·`anchors_checked`·`problems`: `[{"code", "seq"?}]`)·`anchors`(`committed`·`pending` 줄 수)·`legacy_unverified`(`files`·`lines`)·`known_limit`(`"tail_after_last_anchor_not_detected"`)다. 경로·`dir`·`kid`·해시·서명은 넣지 않는다. `status`는 문제가 없고 고정점 디렉터리 누락이 없을 때만 `pass`.

### 4. `VerifySummaryCache`

- `get(key, compute)`: TTL(기본 10초, 주입 가능한 단조 시계) 안이면 저장값을 돌려주고, 아니면 `compute`를 **단일 비행**(스레드 잠금)으로 한 번만 실행해 모든 동시 호출자가 같은 값을 받는다. `compute`가 예외를 던지면 캐시하지 않고 전파한다.

### 5. 테스트(`tests/test_audit_records.py`)

- 합성 체인(임시 키·임시 디렉터리): 다른 턴 ID 이벤트 미포함, 허용 필드 외 키 없음, 64 hex·128 hex·`kid` 값이 결과 어디에도 없음, 필드 형식 방어(이름은 허용인데 값이 형식 밖이면 폐기), `limit` 초과 시 `truncated`, 개행 없는 마지막 줄 무시.
- `verify_summary`: 정상 `pass`, 수정·중간 삭제·순서 변경·고정점 후 꼬리 잘라내기·다른 키 서명 각각 `fail`과 해당 코드(`hash_mismatch`·`seq_gap`·`seq_order` 또는 `prev_hash_mismatch`·`tail_truncated`·`kid_unknown`), 개행 없는 꼬리는 `fail`로 단정하지 않음, 고정점 파일 손상은 `AuditUnavailable`, 반환 키 집합 정확 일치.
- `VerifySummaryCache`: 10개 스레드 동시 호출이 `compute` 1회, TTL 경과 뒤 재계산, 예외 비캐시.
- 읽기 전용: 호출 전후 체인 파일의 바이트·`mtime_ns`가 같다.

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
   - 동결 경로(그래프·프롬프트·도구·Guardrail·평가 러너·골든·감사 쓰기·mock·시드)를 바꾸지 않았는가?
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

- `verify.py`·`log.py`·`crypto.py`를 수정하지 마라. 이유: 감사 쓰기·검증은 동결 경로다. 필요한 비공개 함수는 import해 호출만 한다.
- 체인·고정점·대기 파일에 쓰기를 하지 마라(쓰기 모드 `open`·append·`chmod`·`mkdir`). 임시 디렉터리 스냅샷만 예외다. 이유: 읽기 전용 원칙, 증거 보존(ADR-012).
- `tests/`에서 저장소의 실제 `data/interim/audit`을 읽지 마라. 이유: 비밀·감사 파일 비열람, `conftest.py`의 `protect_repository_audit_artifacts`가 변경을 실패로 본다.
- 식별자(턴 ID·해시)에 PII 정규식을 돌리지 마라. 이유: 형식이 정해진 식별자는 형식 검증 대상이다(FP-010). 무작위 `uuid4().hex` 10,000개(시드 고정)가 통과하는 테스트를 둔다.
- `all()`/`not any()`로 "없음"을 단언할 때 대상이 1개 이상임을 먼저 확인하라. 이유: 빈 집합 공허 통과(FP-069).

## 소비자 점검(시뮬레이션 통과)

`audit/`에 신규 파일 `records.py`만 추가한다. 스텁 파일을 넣은 전체 pytest에서 `tests/test_architecture.py`의 계층 검사(`audit`이 `agent·tools·broker·api`를 import하지 않음)와 `tests/test_docs_consistency.py`(디렉터리 구조 절)는 통과했다(문서 열거 단언이 `records.py`를 요구하지 않음).
