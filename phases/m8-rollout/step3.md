# Step 3: routed-service

## 읽어야 할 파일

먼저 아래 파일들을 읽고 프로젝트의 아키텍처와 설계 의도를 파악하라:

- `/AGENTS.md`(CRITICAL: 대기 중인 `interrupt` thread에 새 입력 금지, 잠금 순서 대화 → task)
- `/docs/M8_DESIGN_DRAFT.md`(2-3절 전체, 특히 "라우터" 절의 메서드 표와 "노출 기록과 롤백 지표 수집")
- `/docs/results/hypotheses-m8-rollout.md`(5~11·14절, 읽기만 한다)
- `/src/govops/api/tasks.py`(전체 758행: 잠금 dict 56~62행, `_resolve` 80~83행, `_launch` 85~91행, `start_conversation` 93~98행, `start` 201~225행, `conversations` 376~416행, `_expire` 418~436행, `approvals` 540~567행, `get` 569~593행, `startup_cleanup` 691~750행, `aclose` 752~758행)
- `/src/govops/api/conversations.py`(`TurnIndex` Protocol 19~35행, `InMemoryTurnIndex`)
- `/src/govops/api/app.py`(라우트가 부르는 서비스 메서드, `app.state.task_service`)
- `/src/govops/agent/graph.py`(`get_task` 1053행~, `build_graph` 시그니처 199~213행과 노드 구성 955~998행, `classify_items` 288~323행, 읽기만 한다)
- `/src/govops/gateway/client.py`(`ClassifyResult` 106~111행, `classify` 385~525행, 읽기만 한다. `api` 계층은 이 모듈을 import하지 않는다)
- `/src/govops/rollout/policy.py`, `/src/govops/rollout/store.py`(step 1), `/src/govops/api/variant.py`(step 2)
- `/tests/test_api_conversations.py`(`TaskService(build_graph(FakeChat…))` 구성), `/tests/test_agent_semantic.py`(`Classifier` 가짜 36~53행), `/tests/test_api_tasks.py`(승인 대기 상태를 만드는 쓰기 프로필 구성)
- `/.claude/skills/failure-patterns/SKILL.md` — 읽기만 한다

## 작업

**수정할 파일**(이 목록이 전부다): `src/govops/api/routed.py`(신규), `tests/test_api_routed.py`(신규). TDD.

열람 금지 경로: `data/interim/`, `data/eval/`, `data/redteam/`, `src/govops/guardrails/rules_v1.json`
수정 허용 경로: `src/govops/api/routed.py`, `tests/test_api_routed.py`
열람 금지의 범위: 이 금지는 **Codex 세션이 해당 경로의 파일을 직접 열람·출력·인용하는 것**에 대한 것이다. 이 step의 코드가 런타임에 그 경로를 다루는 것(예: 기본 출력 경로 `data/interim/rollout/`)은 대상이 아니다. 테스트가 `data/interim/`·`data/eval/`·`data/redteam/`의 실제 파일 **내용**을 읽는 것은 여전히 금지이며 테스트는 임시 디렉터리(`tmp_path`)·인메모리 saver·가짜 chat만 쓴다. 예외로 `data/results/`의 실행 디렉터리는 파일 이름과 JSON 키 이름을 확인하는 데 한해 열람할 수 있고, 응답 본문(`answer`·`effect_bodies`)이나 근거 본문을 출력·인용하지 않는다. 이 제한은 이 step이 새로 작성하는 테스트에 한한 것이며, 기존 `tests/test_m7_freeze.py`가 `scripts/freeze_digest.py`로 해시를 계산하며 동결 경로 파일을 바이트로 읽는 것은 `make verify`의 일부이므로 허용한다. Codex 세션이 그 파일 내용을 출력·인용하는 것은 여전히 금지다.

`src/govops/api/` 아래 새 파일에는 `redteam` 문자열을 쓰지 않는다(`tests/test_redteam_boundary.py`가 `api/`의 모든 `.py`를 검사한다).

**목적**: 이미 만들어진 v1·v2 `TaskService` 두 개를 주입받아 대화 변형에 따라 위임하는 라우터를 만든다. 서비스 조립(`build_task_service`·`open_*`)과 진입점은 다음 step이다. `api/tasks.py`·`api/wiring.py`는 바꾸지 않는다.

### 1. 걸러진 턴 색인

```python
class VariantFilteredTurnIndex:
    def __init__(self, inner: TurnIndex, variants: VariantStore, variant: Literal["v1", "v2"]): ...
```

- `awaiting()`·`list_for_owner(owner_sub, limit)`만 자기 변형의 대화로 거른다(`variants.get_many`로 한 번에 조회, 행이 없는 대화는 `"v1"`). `list_for_owner`는 `inner.list_for_owner(owner_sub, limit)`의 결과를 거른다(공유 색인이 전체 상위 `limit`개를 고르므로 두 변형의 결과를 합치면 정확히 원래 상위 `limit`개다).
- 나머지 `TurnIndex` 메서드(`add`·`turn_ids_of`·`conversation_of`·`set_awaiting`·`mark_deleted`·`is_deleted`·`deleted_conversation_ids`·`delete_conversations`)는 **명시적 메서드로** 그대로 전달한다(`__getattr__` 위임 금지: Protocol의 메서드 목록이 바뀌면 테스트가 잡도록).
- 이유: 거르지 않으면 `conversations`·`approvals`에서 같은 대화가 두 번 나오고, 다른 변형의 대화를 자기 잠금으로 `_expire`한다(잠금 dict는 인스턴스마다 따로, `tasks.py:56-62`).

### 2. 분류 호출 기록 래퍼

```python
class RecordingClassifier:
    def __init__(self, inner, *, directory: Path, monotonic=time.monotonic, now=<UTC now>): ...
    async def classify(self, *, request, task_id, user_id): ...
```

- `inner.classify(request=request, task_id=task_id, user_id=user_id)`를 그대로 부르고 결과를 **그대로** 돌려준다. 예외면 `status="raised"`를 기록하고 같은 예외를 다시 던진다. 인자·결과·예외를 바꾸지 않는다.
- 기록: `store.append_classify(directory, {"ts", "turn_id": task_id, "status": result.status, "started_ms", "ended_ms"})`를 `asyncio.to_thread`로 부른다. `task_id`가 `^[0-9a-f]{32}$`가 아니면 기록하지 않는다. 기록 실패(`OSError`·`ValueError`)는 분류 결과를 바꾸지 않고 표준 오류에 `WARN rollout_classify_record_failed`를 프로세스당 한 번 출력한다.
- `ClassifyResult`를 import하지 않는다(`api`는 `govops.gateway`를 import할 수 있지만 이 래퍼는 `status` 속성만 읽는다).

### 3. 라우터

```python
class RoutedTaskService:
    def __init__(self, v1: TaskService, v2: TaskService, *, turns: TurnIndex, variants: VariantStore, directory: Path, clock=None): ...
    audit  # 속성: v1.audit (__main__.py가 app.state.audit로 읽는다)
    turns  # 속성: 공유(걸러지지 않은) 턴 색인
    async def start(self, user_id, request) -> str: ...
    async def start_conversation(self, user_id, request) -> dict: ...
    async def add_turn(self, user_id, conversation_id, request): ...
    async def delete_conversation(self, user_id, conversation_id) -> dict: ...
    async def conversations(self, user_id, limit): ...
    async def conversation_view(self, user_id, conversation_id): ...
    async def progress(self, user_id, conversation_id): ...
    async def audit_view(self, user_id, conversation_id) -> dict: ...
    async def audit_verify(self) -> dict: ...
    async def approvals(self, user_id): ...
    async def get(self, user_id, task_id): ...
    async def approve(self, user_id, task_id, *, decision, draft_sha256, edited=None): ...
    async def startup_cleanup(self): ...
    async def aclose(self) -> None: ...
```

규칙(설계 2-3절 메서드 표 그대로):
- **배정**(`start`·`start_conversation`): 라우터의 `asyncio.Lock` 하나 안에서 (1) `store.read_state(directory)`, (2) 상태가 `kill=false`이고 `pct_bp>0`이면 롤백 평가(4번), 롤백이면 상태를 다시 읽음, (3) `policy.assign(user_id, state)`, (4) 그 변형 서비스의 같은 메서드 호출, (5) **호출이 돌아온 뒤** `variants.put(conversation_id=…, variant=…, bucket=policy.bucket(user_id), pct_bp=state.pct_bp, assigned_at=<UTC ISO>)`, (6) `store.append_exposure(directory, {"event": "assigned", "ts", "conversation_id", "user_sha256": policy.user_digest(user_id), "bucket", "variant", "pct_bp", "kill"})`. 4에서 예외(`ConversationError`·`ValueError`·`RuntimeError`)면 행·노출 기록 없이 그대로 올린다. 5 실패는 `WARN rollout_variant_write_failed`, 6 실패는 `WARN rollout_exposure_record_failed`(각 프로세스당 한 번)이고 응답은 그대로 돌려준다.
- **대화 위임**(`add_turn`·`delete_conversation`·`conversation_view`·`progress`·`audit_view`): `variants.get_many([conversation_id])`가 `"v2"`이면 v2, 아니면 v1. ID가 `^[0-9a-f]{32}$`가 아니면 조회 없이 v1에 위임한다(v1이 404를 낸다). 행이 없고 v1 그래프에 그 thread가 있으면(`get_task`가 `None`이 아님) `variant_missing` 노출 행을 대화당 프로세스에서 한 번 남긴다.
- **task_id 위임**(`get`·`approve`): ID 형식이 맞으면 `await turns.conversation_of(task_id) or task_id`로 대화를 찾아 위와 같이 고른 서비스에 원래 `task_id`로 위임한다.
- **목록**: `conversations`는 두 서비스 결과를 합친 뒤 `updated_at` 내림차순·`conversation_id` 오름차순으로 다시 정렬하고 `limit`개로 자른다(`InMemoryTurnIndex.list_for_owner`와 같은 두 단계 안정 정렬). `approvals`는 합친 뒤 `(expires_at, task_id)`로 정렬한다. 반환 형태(`{"conversations": [...]}`, `{"approvals": [...]}`)는 `TaskService`와 같다.
- `audit_verify`는 v1에 위임한다(같은 감사 디렉터리). `startup_cleanup`은 **v1에서 한 번만** 부른다. `aclose`는 두 서비스 모두 닫는다(하나가 실패해도 다른 하나를 닫는다).
- `anchor`는 두지 않는다(서버 경로 미사용).

### 4. 롤백 평가

- v2 창: `variants.recent("v2", policy.CANDIDATE_LIMIT)`를 순서대로 보며 v2 그래프의 `get_task` 상태가 `None`·`running`·`awaiting_approval`이 아닌 대화를 `policy.WINDOW`개까지 모은다. v1 대조 창도 같은 규칙으로 v1 그래프에서.
- 분류 기록: `store.read_classify_tail(directory, policy.CLASSIFY_TAIL)`를 읽고 `turns.conversation_of(turn_id)`(평가 한 번 안에서 캐시)로 대화에 연결해 v2 창 대화의 것만 `ConversationOutcome.turns`에 넣는다. `blocked_input` = 상태가 `"blocked_input"`.
- `policy.evaluate(v2, v1)`가 `rollback`이면 `store.write_state(directory, RolloutState(0, True, <UTC ISO>, reasons[0]))`와 `store.append_exposure(directory, {"event": "rollback", "ts", "reason": reasons[0], "pct_bp": <롤백 직전 pct_bp>})`. 상태 쓰기 실패는 예외로 올리지 않고 그 배정을 v1으로 한다(fail-safe).

### 5. 테스트 `tests/test_api_routed.py`

가짜 chat(`test_agent_graph`의 `FakeChat`·`FakeTools`)·`InMemorySaver` 하나를 두 그래프가 공유·`InMemoryTurnIndex` 하나·`InMemoryVariantStore`·`tmp_path` 디렉터리로 구성한다. v2 그래프는 `build_graph(..., semantic=RecordingClassifier(Classifier(...), directory=tmp_path))`, 각 서비스는 `TaskService(graph, turns=VariantFilteredTurnIndex(shared, variants, "v1"|"v2"))`. 모든 대기는 `asyncio.wait_for`로 유한하게(FP-066).

1. pct 0(상태 파일 없음): 대화 여러 개가 전부 v1, `Classifier.calls == []`(Anthropic 호출 0건에 해당).
2. pct 10000: 전부 v2이고 분류 기록이 `classify.jsonl`에 남음.
3. sticky: 대화 생성 뒤 `set` 상당(`store.write_state`)으로 pct 0·kill로 바꿔도 기존 v2 대화의 `add_turn`은 v2 서비스로 간다. 새 대화만 v1.
4. 상태 파일 손상 → 새 대화 v1.
5. 롤백 경로: `Classifier(status="timeout")`으로 v2 대화 20개를 끝까지 실행한 뒤 다음 배정에서 `state.json`이 `kill=true, reason="rollback_classify_abnormal"`, 노출 기록에 `rollback` 행, 그 대화는 v1. 19개면 롤백 없음(`insufficient_v2_sample`).
6. 목록: 두 변형에 걸친 같은 소유자의 대화 목록에 중복이 없고 정렬·`limit`이 원래 공유 색인 결과와 같음. `approvals`도 중복 없음.
7. 잠금 분리: v2 대화가 승인 대기일 때 라우터 `approvals`를 불러도 `v1._conv_locks`에 그 대화 ID가 생기지 않는다(v1이 다른 변형의 대화를 `_expire`하지 않음).
8. 대기 승인이 있는 v2 대화와 v1 대화에 새 턴을 넣으면 둘 다 409 `approval_pending`.
9. task_id 위임: 두 번째 턴의 `task_id`로 `get`하면 그 대화의 변형 서비스가 답한다.
10. 행 없는 대화: v1 그래프에 thread만 있고 행이 없으면 v1으로 위임하고 `variant_missing` 노출 행이 정확히 1개(두 번 불러도 1개).
11. 상태 형태: v2 그래프로 `running` thread를 만든 뒤 라우터 `startup_cleanup`(v1에서 한 번)이 그 thread를 `interrupted`로 바꾸고 v2 서비스의 `conversation_view`가 읽는다. v2 서비스의 `startup_cleanup`은 불리지 않는다(호출 기록으로 확인).
12. 동시 배정: `asyncio.gather`로 `start_conversation` 2개 → 대화 ID 2개가 서로 다르고 변형 행 2개·`assigned` 노출 행 2개(집합 단언, FP-012).
13. 기록 안전: 노출·분류 기록의 모든 줄의 키가 허용 집합 안이고 요청 문장·답변·사용자 라벨 문자열이 파일에 없음.
14. 래퍼: 결과 객체가 `inner`의 반환과 같은 객체(`is`), 예외는 같은 예외로 다시 올라가고 `raised` 기록, 기록 실패여도 결과 동일·`WARN rollout_classify_record_failed` 한 번.
15. 걸러진 색인: `TurnIndex` Protocol의 모든 메서드 이름이 `VariantFilteredTurnIndex`에 명시적으로 정의돼 있음.

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

- `api/tasks.py`·`api/wiring.py`·`api/app.py`·`agent/graph.py`를 수정하지 마라. 라우터에 필요한 것이 이 파일들의 변경 없이는 불가능하면 `blocked`로 보고하라. 이유: 기존 함수 본문 불변이 원칙이고(설계 1-2절) 이 step의 수정 파일은 두 개뿐이다(FP-054).
- 승인 대기 중인 대화에 라우터가 직접 `ainvoke`·`aupdate_state`를 부르지 마라. 모든 상태 변경은 변형 서비스의 공개 메서드를 거친다. 이유: 대기 중인 `interrupt` thread에 새 입력을 넣으면 대기 승인이 버려진다(AGENTS.md CRITICAL).
- 다른 변형의 대화를 그 서비스가 아닌 서비스의 메서드로 처리하지 마라(목록 포함). 이유: 잠금 dict가 인스턴스마다 따로라 잠금 순서 보증이 깨진다(설계 2-3절).
- 동시 실행 결과를 `gather` 인덱스로 단언하지 마라. 이유: 순서는 스케줄링에 달렸다(FP-012).
- 롤백 경로를 PASS 경로만 테스트하지 마라. 임계 초과 경로를 실제로 실행해 상태 파일이 바뀌는 것을 단언하라. 이유: SKIP·PASS만으로는 롤백 자동화가 검증되지 않는다(FP-073, 설계 6절 14행).
