# Step 4: outbox-executor

## 읽어야 할 파일

- `/AGENTS.md`, `/docs/ADR.md` (ADR-006, ADR-011, ADR-017 5항), `/docs/ARCHITECTURE.md` ("에이전트 상태 전이"의 실행 상태 그림, "도구와 부작용·멱등성", "상태 관리"), `/docs/THREAT_MODEL.md` (TH6, V8, "5. 장애 주입 시나리오"), `/docs/EVAL.md` ("안정성 (M3)")
- 이전 step 산출물: `/src/govops/mocks/faults.py`, `/src/govops/mocks/admin.py`(`state --bodies`), `/src/govops/tools/rest.py`, `/src/govops/tools/slack.py`, `/src/govops/tools/jira.py`, `/src/govops/tools/markers.py`, `/src/govops/tools/resilience.py`, `/src/govops/audit/log.py`
- `/src/govops/agent/checkpointer.py`(Postgres 연결 방식 참고), `/src/govops/eval/observation.py`, `/src/govops/eval/tcr.py`(`effect_counts`), `/tests/test_e2e_isolation.py`(in-process mock 연결 방식)

## 작업

부작용 **outbox**와 **실행기**, 그리고 중복·누락 건수를 세는 순수 함수를 만든다. 에이전트 그래프에는 아직 연결하지 않는다(다음 phase). TDD로 테스트를 먼저 쓴다.

### outbox (`src/govops/tools/outbox.py`)

```python
class OutboxState(StrEnum): RECORDED; ATTEMPTED; UNCERTAIN; SUCCEEDED; FAILED
@dataclass(frozen=True) class OutboxRow: idempotency_key; task_id; tool; target; args_sha256; approval_id; state; attempts: int; mock_id: str | None; audit_gap: bool; reason: str | None
class OutboxStore(Protocol):
    async def claim(self, row: OutboxRow) -> tuple[OutboxRow, bool]   # (현재 행, 새로 선점했는가). 원자적: 같은 키는 한 번만 선점
    async def update(self, key: str, **changes) -> OutboxRow
    async def get(self, key: str) -> OutboxRow | None
    async def list_open(self) -> list[OutboxRow]      # RECORDED·ATTEMPTED·UNCERTAIN (재시작 정리용)
class InMemoryOutboxStore: ...
class PostgresOutboxStore: ...      # govops 앱 DB, 테이블 자동 생성(CREATE TABLE IF NOT EXISTS), 키는 PRIMARY KEY, claim은 INSERT … ON CONFLICT DO NOTHING
```

- 행에는 **해시·mock ID·상태만** 둔다. 본문·인자 원문은 두지 않는다(원문은 checkpointer 상태에 있다, ADR-015).
- `PostgresOutboxStore`는 `GOVOPS_DATABASE_URL`의 `govops` DB를 쓴다(psycopg, checkpointer와 같은 드라이버). 오류 메시지에 접속 문자열·비밀번호를 노출하지 않는다(checkpointer의 방식을 따른다). `make verify`는 Postgres 없이 통과해야 하므로 **Postgres 구현의 계약 테스트는 환경 변수 `GOVOPS_TEST_DATABASE_URL`이 있을 때만 돌고 없으면 skip**한다. 같은 계약 테스트 모음을 InMemory 구현에는 항상 돌린다. `Makefile`에 사용자 실행용 타깃 `test-outbox-pg`를 추가한다(`.PHONY` 등록, 주석에 "compose Postgres 기동·`make db-init` 후 사용자 실행, 테스트 전용 DB URL 필요"). 이 타깃은 `uv run --env-file .env pytest tests/test_outbox_pg.py -q`처럼 해당 계약 테스트만 돌리되 `GOVOPS_TEST_DATABASE_URL`이 비어 있으면 안내 문구와 함께 실패한다. `make verify`에는 넣지 않는다. 테스트는 테스트 전용 스키마/테이블 접두어를 쓰고 끝나면 정리한다(운영 outbox 테이블을 건드리지 않는다).

### 실행기 (`src/govops/tools/effects.py`)

```python
@dataclass(frozen=True) class EffectRequest: task_id; user_id; tool; args: dict; approval_id: str
@dataclass(frozen=True) class EffectResult: state: OutboxState; mock_id: str | None; sends: int; lookups: int; audit_gap: bool; reason: str | None
async def execute_effect(req, *, jira, slack, outbox, audit: AuditPort, policy: ToolPolicy, sleep=...) -> EffectResult
async def reconcile_effect(key, *, user_id, ... ) -> EffectResult     # 재시작 정리용. 표식 조회만 하고 절대 재발송하지 않는다
```

`execute_effect`의 순서와 규칙(핵심 규칙이므로 그대로 구현한다):

1. 도구 이름이 부작용 3종(`slack_send_message`·`jira_add_comment`·`jira_create_followup_ticket`)이고 `args`가 해당 입력 모델(`tools/registry.py`)로 검증되는지 확인한다. 아니면 호출 없이 실패.
2. 멱등 키·표식(`markers`)을 만든다. 대상(`target`)은 채널 ID 또는 티켓 ID(후속 티켓은 **원 티켓 ID**)다.
3. `outbox.claim(...)`: 선점하지 못했으면 **호출하지 않고** 현재 행을 돌려준다. `SUCCEEDED`면 그 결과(`reason="already_succeeded"`), `RECORDED`·`ATTEMPTED`·`UNCERTAIN`(다른 호출이 진행 중이거나 이전 실행이 중단됨)이면 `reason="in_progress"`, `FAILED`면 `reason="already_failed"`다(재실행은 새 승인·새 task에서만). 선점하지 못한 호출도 `audit.append_with_retry("side_effect.result", …, claimed=False, reason=<위 코드>)`를 남긴다.
4. 호출 루프(**최대 3회 발송 시도**, `send_no` = 1, 2, 3):
   - **POST마다 직전에** `audit.append("side_effect.attempt", …, send_no=n)`을 fail-closed로 기록한다(재시도 없음). 기록에 실패하면 **그 발송은 하지 않는다**. 아직 아무것도 보내지 않았으면(`send_no == 1`, 이전 발송 없음) `FAILED`(`audit_write_failed`), 이전 발송이 하나라도 있었으면(`send_no >= 2`) `UNCERTAIN`(`audit_write_failed`)으로 끝낸다. 예외를 올리지 않고 `EffectResult`로 돌려준다.
   - 기록에 성공하면 outbox를 `ATTEMPTED`로 갱신하고 `policy.call_once`로 POST(표식을 `metadata`에 포함)한다.
   - 2xx → `SUCCEEDED`(mock ID 저장).
   - `DEFINITIVE`(4xx) → `FAILED`(재시도 없음, 표식 조회 없음).
   - `CIRCUIT_OPEN` → 호출하지 않았다. 이전에 불확실한 시도가 없으면 `FAILED`(`circuit_open`), 있으면 `UNCERTAIN`.
   - `CONNECT`(요청이 닿지 않았음) → **표식 조회 없이** 백오프 후 다음 발송 시도(바로 재시도).
   - `RATE_LIMITED`(429) → **표식 조회 없이** `Retry-After`(상한 적용)를 지키고 다음 발송 시도.
   - `UNCERTAIN`(타임아웃·5xx·전송 오류) → outbox `UNCERTAIN`으로 갱신하고 **다음 발송 전에 항상 표식 2단계 조회**: 1단계 조회 → 표식 있으면 `SUCCEEDED`(재발송 없음) → 없으면 `LOOKUP_WAIT`(2.0초) 대기 → 2단계 재조회 → 표식 있으면 `SUCCEEDED` → **두 번 모두 없을 때만** 백오프 후 다음 발송 시도로 간다. 표식 조회 자체가 실패하면(오류·서킷 열림) **재발송하지 않고** `UNCERTAIN`으로 끝낸다. 발송 시도가 남아 있지 않으면(3회째가 불확실 실패) 표식 조회를 마친 뒤에도 표식이 안 보이면 `UNCERTAIN`으로 끝낸다.
   - 조회 방식: slack은 `channel_history`, 코멘트는 `ticket_comments`, 후속 티켓은 원 티켓의 `ticket_children`을 `markers.has_marker`로 판정한다. 검색 API 금지.
   - 조회마다 `audit.append_with_retry("side_effect.marker_lookup", …)`(끝내 실패하면 `audit_gap=True`), 호출마다 `obs.trace`의 `SpanKind.IDEMPOTENCY_LOOKUP` span.
5. **끝 상태 규칙**: `FAILED`는 **"보내지 않았음이 확실한" 경우에만** 쓴다(4xx 확정 거절, 서킷 열림으로 호출하지 않음, 연결 실패·429만 반복되어 시도를 다 씀, 첫 발송 전 감사 실패). **마지막 시도가 불확실 실패(타임아웃·5xx·전송 오류)로 끝났으면 표식이 안 보여도 `UNCERTAIN`**으로 끝내고, `reconcile_effect`가 나중에 확정한다.
6. 종료 시 `audit.append_with_retry("side_effect.result", …)`로 결과를 기록한다(`claimed=True`). 끝내 실패하면 outbox 행의 `audit_gap=True`로 표시하고 `EffectResult.audit_gap`에 반영한다(부작용은 이미 일어났으므로 되돌리지 않는다).
7. `reconcile_effect`: 열린 행(`RECORDED`·`ATTEMPTED`·`UNCERTAIN`)의 표식을 2단계로 조회해 있으면 `SUCCEEDED`, 두 번 모두 없으면 `FAILED`(`unsent_confirmed`), 조회 불가면 상태 유지. **어떤 경우에도 POST를 보내지 않는다.** `OutboxStore.list_open`은 이 세 상태를 모두 돌려준다.

값(`LOOKUP_WAIT`=2.0초, `MAX_SENDS`=3 발송 시도)은 `effects.py` 상수 한 곳에 둔다.

### 중복·누락 건수 (`src/govops/eval/duplicates.py`, 순수 함수, `mocks`를 import하지 않는다)

```python
@dataclass(frozen=True) class EffectCounts: duplicates: int; missing: int; per_key: dict[tuple[str, str, str], tuple[int, int]]; unapproved: int; unmarked: int
def count_effects(reports: Mapping[str, Mapping], *, task_id: str, expected: Sequence[Effect]) -> EffectCounts
```

- 입력은 mock `/state?bodies=true` 보고서 dict(`jira`·`slack`)이고, 효과를 (task, 종류, 대상)으로 묶어 센다. **멱등 키가 아니라 mock 상태와 `govops_task` 표식으로 센다**(키가 다르게 생긴 중복도 잡는다).
- 중복 = Σ max(0, 실제 − 기대), 누락 = Σ max(0, 기대 − 실제). 표식이 없거나 다른 task의 표식인 효과는 현재 task에 귀속시켜 센다(보수적)하고 `unmarked`로도 보고한다. `govops_approval` 표식이 없는 효과 수는 `unapproved`다.
- 기대는 `eval.spec`의 `Effect`(kind·target·count)를 쓴다. 효과 종류 매핑: slack 메시지 → `slack_message`, 코멘트 → `jira_comment`, 후속 티켓 → `jira_followup_ticket`(대상은 원 티켓 ID).

### 장애 시나리오 매트릭스 테스트 (`tests/test_effects_matrix.py`)

in-process mock(ASGI)과 step 0의 `FaultState`, 주입 시계·sleep(대기 시간을 시계에 더해 실시간 대기 없음)으로 세 도구(slack·코멘트·후속 티켓) × 아래 시나리오를 돌린다. 클라이언트 쪽 장애(F4·F5의 응답 유실)는 ASGI 전송을 감싼 테스트용 transport(요청 전 `ConnectError`, 응답을 받은 뒤 `ReadError`)로 만든다. 각 시나리오는 **중복 건수·누락 건수·최종 outbox 상태·POST 호출 수·표식 조회 수**를 단언한다.

| 시나리오 | 주입 | 기대 |
|---|---|---|
| F1 Soft 초과 | 4초 지연(처리 후 정상 응답) | 효과 1, POST 1, 조회 0, soft 속성 기록 |
| F1 Hard 초과 | 처리 후 12초 지연(Hard 값은 정책 생성자로 주입) | 타임아웃→표식 조회에서 발견, 효과 1, POST 1 |
| F2 처리 전 5xx 1회 | 1번째 요청 500(미반영) | 조회 2회 모두 없음→재발송, 효과 1, POST 2 |
| F2 처리 후 5xx | 반영 후 500 | 조회에서 발견, 재발송 0, 효과 1 |
| F2 연속(발송 엔드포인트만) | `slack.send`(코멘트·후속 티켓은 해당 create)만 모든 요청 500(미반영), 조회는 정상 | 발송 3회를 다 쓰고 매번 조회 2회 모두 없음, 마지막 시도가 불확실 실패이므로 `UNCERTAIN`, 효과 0(누락 1). 서킷은 단언하지 않는다(조회 GET 성공이 실패 횟수를 되돌린다) |
| 서킷 열림 | 발송·조회 엔드포인트 **모두** 실패로 걸고 서로 다른 효과(다른 task)를 이어 실행 | 연속 실패가 5회에 이르러 서킷 열림, 그 뒤 효과는 **호출 0**·`FAILED`(`circuit_open`), 열린 뒤 대상 서비스 요청 수 증가 없음 |
| F3 | 429 + `Retry-After: 1` 1회 | 조회 0, 대기 ≥ 1초, 효과 1 |
| F4 | 첫 요청 연결 실패(클라이언트 transport) | 조회 0, 바로 재시도, 효과 1 |
| F5 | 반영 후 응답 유실 | 조회에서 발견, 재발송 0, 효과 1 |
| F5 + 반영 지연 짧게 | F5 + `hide_for` 1.0초 | 1단계 없음→대기→2단계 발견, 효과 1, 중복 0 |
| F5 + 반영 지연 길게 | F5 + `hide_for` 6.0초 | 1회차 응답 유실→두 조회 모두 없음→발송 시도가 남아 재발송→**중복 1**(테스트 이름에 `known_limit`). 이 시나리오에서 재발송 뒤 응답이 정상이어야 `SUCCEEDED`가 되고, 재발송도 유실로 끝나면 `UNCERTAIN` |
| 중단 후 재실행 | outbox가 `ATTEMPTED`인 채 같은 키로 재실행 | 호출 0, `in_progress`, `claimed=false` 결과 기록 |
| 동시 호출 | 같은 키 `asyncio.gather` 2회 | 효과 1 |
| 정리 | `UNCERTAIN` 행을 `reconcile_effect` | 표식 있으면 `SUCCEEDED`, 없으면 `FAILED`, POST 0 |
| 감사 의도 실패(첫 발송) | 첫 `side_effect.attempt` 쓰기 실패 | POST 0, `FAILED`(`audit_write_failed`), 효과 0 |
| 감사 의도 실패(재발송 직전) | 1회차 불확실 실패 뒤 2회차 `attempt` 쓰기 실패 | 추가 POST 0, `UNCERTAIN`(`audit_write_failed`) |
| 선점 실패 | `SUCCEEDED`·`FAILED`·진행 중 행이 있는 키로 재실행 | POST 0, `side_effect.result`에 `claimed=false`와 `reason` 기록 |
| 감사 결과 실패 | 결과 기록만 실패 | 부작용 1, `audit_gap=True` |

- **F10 단독 시나리오를 만들지 않는다**(조회 반영 지연만으로는 표식 조회가 일어나지 않는다). 위 표처럼 항상 F5와 결합한다.
- 채널 대응 테스트: 정답지(`data/eval/tasks.json`)의 슬랙 대상 이름(`team-sre`·`announce` 등)이 mock 채널 시드(`mocks.slack.CHANNELS`의 `channel_id`)와 **같은 문자열**임을 모든 slack 효과 기대 항목에 대해 확인한다(테스트 파일이므로 `govops.mocks`·`data/eval` 읽기 허용). 티켓 대상 ID도 `data/seed/tickets.json`에 실제로 있는지 확인한다.
- `count_effects` 단위 테스트: 키가 다른 중복·표식 없는 효과·승인 표식 없는 효과·누락을 각각 센다.
- 키·네트워크 없이 통과한다. 계층 가드 통과(`tools/`는 `agent`·`mocks` 등을 import하지 않는다 — 테스트 파일은 예외).

## Acceptance Criteria

```bash
make verify   # ruff check + ruff format --check + pytest + web 검사
```

## 검증 절차

1. 위 AC 커맨드를 실행한다.
2. 아키텍처 체크리스트를 확인한다:
   - ARCHITECTURE.md의 실행 상태 그림(의도 기록 → outbox → 호출 → 표식 조회 → 결과 기록)을 그대로 따르는가?
   - 재시도 전 표식 조회가 모든 UNCERTAIN 경로에 있고, 바로 재시도는 연결 실패·429뿐인가?
   - AGENTS.md CRITICAL 규칙을 위반하지 않았는가?
3. 결과를 최종 응답(JSON)으로 보고한다. `phases/` 아래 파일은 수정하지 않고 git commit도 하지 않는다:
   - 성공 → `"status": "completed"`, `"summary": "산출물 한 줄 요약"`
   - 해결할 수 없는 실패 → `"status": "error"`, `"reason": "구체적 에러 내용"`
   - 사용자 개입 필요 → `"status": "blocked"`, `"reason": "구체적 사유"` 후 즉시 중단

## 금지사항

- 5xx·타임아웃 뒤에 표식 조회 없이 재발송하지 마라. 이유: "서버는 처리했는데 응답 유실"에서 중복이 생긴다(절대 규칙 1).
- 표식 조회를 두 번 모두 확인하기 전에 재발송하지 마라. 조회가 실패했을 때도 재발송하지 마라. 이유: 확인되지 않은 상태에서 보내는 것보다 `UNCERTAIN`으로 남기는 편이 안전하다.
- `reconcile_effect`가 POST를 보내게 하지 마라. 이유: 재시작 정리는 자동 재발송을 하지 않는다.
- outbox에 본문·인자 원문을 저장하지 마라. 이유: 원문은 checkpointer에만 두고 7일 뒤 삭제한다(ADR-015).
- 감사 의도 기록 전에 outbox를 선점하거나 호출하지 마라. 이유: 의도를 기록하지 못하면 실행하지 않는다.
- 이 실행기를 에이전트 그래프·`ToolRuntime`·API에 연결하지 마라. 이유: 연결은 승인 노드가 생기는 다음 phase에서 한다. 승인 없이 실행되는 경로를 만들면 안 된다.
- F10을 단독 시나리오로 만들거나 `/state`의 실제 건수 대신 조회 결과로 중복을 세지 마라.
- `eval/duplicates.py`가 `mocks`를 import하게 하지 마라(보고서 dict만 받는다). 이유: 계층 경계.
- 실시간 `sleep`에 의존하는 테스트를 만들지 마라.
- `scripts/`, `phases/`, `web/`, `data/contract/`, `data/eval/`을 수정하지 마라.
- `.env`와 A의 `data/interim/mcp/tokens.json`을 열거나 출력하지 마라. 토큰·키·접속 문자열을 로그·오류·테스트에 남기지 마라.
- 기존 테스트를 깨뜨리지 마라. 계층 가드를 약화하지 마라.
