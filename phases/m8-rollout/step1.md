# Step 1: rollout-policy

## 읽어야 할 파일

먼저 아래 파일들을 읽고 프로젝트의 아키텍처와 설계 의도를 파악하라:

- `/AGENTS.md`(계층 경계 표: `metrics`·`regress` 행의 형식)
- `/docs/M8_DESIGN_DRAFT.md`(2-3절 전체)
- `/docs/results/hypotheses-m8-rollout.md`(step 0이 만든 등록 문서. 이 step이 구현하는 값·이름의 단일 출처, 읽기만 한다)
- `/tests/test_architecture.py`(`LAYERS`, `FORBIDDEN_IMPORTS`, `METRICS_CONSUMERS` 패턴, `test_rules_cover_actual_layer_directories`)
- `/tests/test_env_defaults.py`(`test_variable_defaults`의 표)
- `/src/govops/env.py`(`env_or`)
- `/src/govops/eval/redteam_judge.py`(`nearest_rank_p`, 읽기만 한다. import하지 않는다)
- `/.claude/skills/failure-patterns/SKILL.md` — 읽기만 한다

## 작업

**수정할 파일**(이 목록이 전부다): `src/govops/rollout/__init__.py`(신규), `src/govops/rollout/policy.py`(신규), `src/govops/rollout/store.py`(신규), `tests/test_rollout_policy.py`(신규), `tests/test_rollout_store.py`(신규), `tests/test_architecture.py`(`rollout` 계층 규칙 추가에 한함, 기존 규칙 약화 금지), `tests/test_env_defaults.py`(`test_variable_defaults` 표에 행 하나 추가에 한함), `AGENTS.md`(계층 경계 표에 `rollout` 행 하나 추가에 한함), `tests/test_metrics_toolcall.py`(`FORBIDDEN_IMPORTS["metrics"]`를 정확히 단언하는 테스트의 `forbidden` 집합(359~370행)에 `"govops.rollout"` 한 줄 추가에 한함, 그 밖의 단언 불변. 380~388행의 소비자 루프는 `LAYERS`를 따라가므로 고치지 않는다). TDD.

열람 금지 경로: `data/interim/`, `data/eval/`, `data/redteam/`, `src/govops/guardrails/rules_v1.json`
수정 허용 경로: `src/govops/rollout/__init__.py`, `src/govops/rollout/policy.py`, `src/govops/rollout/store.py`, `tests/test_rollout_policy.py`, `tests/test_rollout_store.py`, `tests/test_architecture.py`, `tests/test_env_defaults.py`, `AGENTS.md`, `tests/test_metrics_toolcall.py`
열람 금지의 범위: 이 금지는 **Codex 세션이 해당 경로의 파일을 직접 열람·출력·인용하는 것**에 대한 것이다. 이 step의 코드가 런타임에 그 경로를 다루는 것(예: 기본 출력 경로 `data/interim/rollout/`)은 대상이 아니다. 테스트가 `data/interim/`·`data/eval/`·`data/redteam/`의 실제 파일 **내용**을 읽는 것은 여전히 금지이며 테스트는 임시 디렉터리만 쓴다(`GOVOPS_ROLLOUT_DIR`를 `tmp_path`로 두거나 경로 인자를 넘긴다). 예외로 `data/results/`의 실행 디렉터리는 파일 이름과 JSON 키 이름을 확인하는 데 한해 열람할 수 있고, 응답 본문(`answer`·`effect_bodies`)이나 근거 본문을 출력·인용하지 않는다. 이 제한은 이 step이 새로 작성하는 테스트에 한한 것이며, 기존 `tests/test_m7_freeze.py`가 `scripts/freeze_digest.py`로 해시를 계산하며 동결 경로 파일을 바이트로 읽는 것은 `make verify`의 일부이므로 허용한다. Codex 세션이 그 파일 내용을 출력·인용하는 것은 여전히 금지다.

**목적**: 등록 문서의 배정·평가 규칙을 순수 함수로, 상태·기록 파일 입출력을 작은 저장 모듈로 만든다. 서비스·DB·그래프는 이 step에서 다루지 않는다.

### 1. 계층 규칙 (`tests/test_architecture.py`, `AGENTS.md`)

- `LAYERS`에 `"rollout"`을 더하고 `FORBIDDEN_IMPORTS["rollout"]`을 둔다: `govops.agent`, `govops.api`, `govops.broker`, `govops.tools`, `govops.gateway`, `govops.mocks`, `govops.audit`, `govops.guardrails`, `govops.eval`, `govops.obs`, `langgraph`, `fastapi`, `psycopg`, LLM SDK(`LLM_SDK`). 기존 루프가 `govops.evalrun`·`govops.grounding`·`govops.metrics`를 자동으로 더한다(그대로 둔다).
- `METRICS_CONSUMERS` 패턴처럼 `ROLLOUT_CONSUMERS: set[str] = {"api"}`를 두고 `LAYERS - {"rollout"} - ROLLOUT_CONSUMERS`의 모든 계층에 `govops.rollout`을 금지로 더한다.
- `AGENTS.md` 계층 경계 표에 행 하나: `| 롤아웃 | \`rollout/\` | M8 항목 4 v2 점진 배포의 순수 정책(버킷·배정·롤백 평가)·상태·노출 기록 파일·운영 CLI(M8 추가 측정, 공식 M6 수치 아님) | \`govops.agent\`, \`govops.api\`, \`govops.broker\`, \`govops.tools\`, \`govops.gateway\`, \`govops.mocks\`, \`govops.audit\`, \`govops.guardrails\`, \`govops.eval\`, \`govops.obs\`, \`govops.evalrun\`, \`govops.grounding\`, \`govops.metrics\`, \`langgraph\`, \`fastapi\`, \`psycopg\`, LLM SDK; \`api\` 외 다른 계층은 \`rollout\` import 금지 |`. 위치는 `회귀` 행 바로 뒤. 다른 행·`CRITICAL:` 문장 수는 바꾸지 않는다(`tests/test_docs_consistency.py`가 `CRITICAL:` 26개를 단언한다).

### 2. `src/govops/rollout/policy.py` (순수, 파일·시간·환경 읽기 없음)

시그니처(이름은 등록 문서 14절과 같아야 한다):

```python
SALT: str = "govops-m8-rollout-v1"
WINDOW: int = 20
MIN_SAMPLE: int = 20
CANDIDATE_LIMIT: int = 60
CLASSIFY_TAIL: int = 5000
ABNORMAL_RATE_MAX: float = 0.05
BLOCK_DIFF_MAX: float = 0.10
P95_MS_MAX: float = 8000.0
ABNORMAL_STATUSES: frozenset[str]  # {"timeout","rate_limited","error","parse","raised"}
CLASSIFY_STATUSES: frozenset[str]  # ABNORMAL_STATUSES | {"ok","v1_blocked"}
ROLLBACK_CODES: tuple[str, ...]    # ("rollback_no_classify_calls","rollback_classify_abnormal","rollback_block_diff","rollback_classify_p95")

@dataclass(frozen=True)
class RolloutState:
    pct_bp: int
    kill: bool
    rolled_back_at: str | None
    reason: str | None

OFF: RolloutState  # RolloutState(0, False, None, None)

def user_digest(user_label: str) -> str: ...      # sha256((SALT+label).encode()).hexdigest()
def bucket(user_label: str) -> int: ...           # int(user_digest[:8], 16) % 10000
def assign(user_label: str, state: RolloutState) -> Literal["v1", "v2"]: ...

@dataclass(frozen=True)
class ClassifyCall:
    status: str
    started_ms: float
    ended_ms: float

@dataclass(frozen=True)
class ConversationOutcome:
    conversation_id: str
    blocked_input: bool
    turns: Mapping[str, Sequence[ClassifyCall]]   # turn_id → 그 턴의 분류 호출

@dataclass(frozen=True)
class Evaluation:
    action: Literal["none", "rollback"]
    reasons: tuple[str, ...]        # ROLLBACK_CODES의 부분열, 등록 문서 11절 순서
    notes: tuple[str, ...]          # "insufficient_v2_sample", "insufficient_v1_control"
    abnormal_rate: float | None
    block_diff: float | None
    p95_ms: float | None

def union_ms(calls: Sequence[ClassifyCall]) -> float: ...   # 구간 합집합 길이, 빈 목록 0.0
def nearest_rank_p95(values: Sequence[float]) -> float | None: ...
def evaluate(v2: Sequence[ConversationOutcome], v1: Sequence[ConversationOutcome]) -> Evaluation: ...
```

규칙(등록 문서 3·10·11절 그대로):
- `assign`: `state.kill`이면 `"v1"`, `bucket(label) < state.pct_bp`이면 `"v2"`, 아니면 `"v1"`. `pct_bp`가 0~10000 정수가 아니면 `ValueError`.
- `evaluate`는 입력의 앞 `WINDOW`개만 쓴다(호출자가 창을 고른다). v2가 `MIN_SAMPLE` 미만이면 `action="none"`, `notes=("insufficient_v2_sample",)`, 지표 `None`. 분류 호출 0건이면 `rollback_no_classify_calls`(이 경우 비정상 비율·p95는 `None`). 비교는 엄격한 초과. v1 대조가 `MIN_SAMPLE` 미만이면 차단 기준만 건너뛰고 `insufficient_v1_control`을 `notes`에 넣는다(`block_diff=None`). `v1_blocked`는 비정상 비율의 분모에만 든다. 정의 밖 `status`는 `ValueError`.
- p95는 턴마다 `union_ms`를 구한 값들의 nearest-rank(`ordered[ceil(n*0.95)-1]`)다. `govops.eval`을 import하지 않고 같은 식을 이 모듈에 둔다(계층 규칙).

### 3. `src/govops/rollout/store.py` (파일 입출력만)

```python
def rollout_dir() -> Path: ...    # Path(env_or("GOVOPS_ROLLOUT_DIR", "data/interim/rollout"))
def read_state(directory: Path) -> RolloutState: ...
def write_state(directory: Path, state: RolloutState) -> None: ...
def append_exposure(directory: Path, row: Mapping[str, object]) -> None: ...
def append_classify(directory: Path, row: Mapping[str, object]) -> None: ...
def read_exposures(directory: Path) -> list[dict]: ...
def read_classify_tail(directory: Path, limit: int = CLASSIFY_TAIL) -> list[dict]: ...
```

- 파일 이름 `state.json`·`exposures.jsonl`·`classify.jsonl`. 디렉터리는 없으면 0700으로 만들고 파일은 0600.
- `read_state`: 파일 없음 → `OFF`. JSON 오류·키 집합이 `{"pct_bp","kill","rolled_back_at","reason"}`와 다름·타입 오류(`pct_bp`는 `bool`이 아닌 `int` 0~10000, `kill`은 `bool`) → `RolloutState(0, True, None, "state_invalid")`. 예외를 밖으로 던지지 않는다.
- `write_state`: 같은 디렉터리의 임시 파일에 쓰고 `fsync` 뒤 `os.replace`. 실패하면 `OSError`를 그대로 올린다(CLI가 고정 코드로 바꾼다).
- `append_exposure`·`append_classify`: 한 줄 JSON을 `O_APPEND`로 쓰고 `fcntl.flock`으로 감싼다. **허용 키 밖이면 `ValueError`**(본문·토큰이 들어갈 자리를 만들지 않는다). 노출: `event ∈ {"assigned","variant_missing","rollback"}`이고 이벤트별 키는 등록 문서 7절과 정확히 같다(`ts` 포함). 분류: 키 정확히 `{"ts","turn_id","status","started_ms","ended_ms"}`, `status ∈ CLASSIFY_STATUSES`. `conversation_id`·`turn_id`는 `^[0-9a-f]{32}$`, `user_sha256`은 `^[0-9a-f]{64}$`(형식 검증, PII 정규식을 돌리지 않는다, FP-010).
- `read_exposures`·`read_classify_tail`: 없으면 `[]`. 깨진 줄은 건너뛰지 않고 `ValueError`(조용히 줄이지 않는다).

### 4. 테스트

`tests/test_rollout_policy.py`: 버킷 결정론(같은 라벨 같은 값, 0~9999), 시드 고정(`random.Random(20261021)`) 무작위 라벨 10,000개에서 `pct_bp=1000`의 v2 비율이 0.10±0.01 안, pct 0이면 전원 v1, 10000이면 전원 v2, kill이면 전원 v1, `union_ms`(겹침·떨어짐·포함·빈 목록), nearest-rank 경계, `evaluate` 경계값(비율이 정확히 0.05면 미롤백, 0.05 초과면 롤백; 차 0.10 정확/초과; p95 8000 정확/초과), v2 19개면 `insufficient_v2_sample`, v1 19개면 차단 기준만 보류, 분류 0건 롤백, `v1_blocked`가 분모에만 드는 것, 사유 여럿의 순서, 정의 밖 status `ValueError`, **등록 문서의 코드 목록(14절)과 `ROLLBACK_CODES`·`ABNORMAL_STATUSES`·`CLASSIFY_STATUSES`·상수 값이 같음**(문서 텍스트에서 코드 이름을 찾는다).
`tests/test_rollout_store.py`: 파일 없음 → `OFF`, 손상 4종(JSON 오류·키 추가·키 누락·`pct_bp` 범위 밖/`bool`) → `state_invalid`, 원자 쓰기 뒤 권한 0600·디렉터리 0700, 허용 키 밖·형식 밖 ID·본문 키(`request`·`answer`·`token`) 거부, 깨진 줄 `ValueError`, 꼬리 읽기 개수, `GOVOPS_ROLLOUT_DIR` 빈 값이면 기본값(FP-030), 스레드 8개 동시 `append_exposure` 뒤 줄 수 = 8이고 각 줄이 유효 JSON(집합 단언, FP-012).
`tests/test_env_defaults.py`: `test_variable_defaults` 표에 `("rollout/store.py", "GOVOPS_ROLLOUT_DIR", "data/interim/rollout")` 한 행.

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
   - 동결 경로(그래프·프롬프트·도구·Guardrail·평가 러너·골든·감사 쓰기·mock·시드·`costs.py`·`infra/litellm/config.yaml`)를 바꾸지 않았는가?
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

- `docs/results/hypotheses-m8-rollout.md`를 수정하지 마라. 문서와 코드가 다르면 코드를 문서에 맞추고, 문서가 틀렸다고 판단되면 `blocked`로 보고하라. 이유: 등록 문서는 동결이다(FP-043).
- `all(...)`·`== 0`으로 "문제 없음"을 판정할 때 대상이 0개인 경우를 통과로 두지 마라. 이유: 분류 0건·창 0개는 공허 통과다(FP-069). 등록 규칙의 `rollback_no_classify_calls`·`insufficient_v2_sample`로 처리한다.
- 식별자(`conversation_id`·`turn_id`·`user_sha256`)에 PII 정규식을 돌리지 마라. 형식으로만 검증하라. 이유: 무작위 hex가 PII 규칙에 걸린다(FP-010).
- 동시 쓰기 테스트에서 줄 순서를 단언하지 마라. 이유: 순서는 스케줄링에 달렸다(FP-012). 줄 수·각 줄 유효성·값 집합으로 단언한다.
- `govops.eval`의 `nearest_rank_p`를 import하지 마라. 이유: `rollout` 계층은 `govops.eval` import 금지다(1번 규칙).
