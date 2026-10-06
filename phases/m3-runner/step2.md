# Step 2: approval-driver

## 읽어야 할 파일

- `/AGENTS.md`, `/docs/ADR.md` (ADR-010·011·018·019), `/docs/EVAL.md` ("TCR", "golden trace", "재실행 규칙"), `/docs/ARCHITECTURE.md`("에이전트 상태 전이"), `/docs/results/hypotheses.md`(고정 문장 5·§9·§10), `/.claude/skills/failure-patterns/SKILL.md`
- 계층 규칙: `evalrun/`은 `broker`·`mocks`·`audit`·`guardrails`·LLM SDK를 import하지 않고 다른 계층은 `evalrun`을 import하지 않는다(`AGENTS.md` 계층 경계 표). 확인한 사실의 근거 파일: `src/govops/eval/run_grade.py`(`build_observation`은 상태가 `completed`·`failed`가 아니면 거부하고 `effect_bodies=[]`), `src/govops/eval/observation.py`(`Observation`·`ApprovalRecord`·`ObservedEffect`), `src/govops/mocks/jira.py`·`slack.py`(`report(bodies=)`), `src/govops/tools/markers.py`, `src/govops/api/tasks.py`(`_view`), `src/govops/api/app.py`(`ApprovalRequest`), `data/eval/tasks.json`(`approval_script`), `data/seed/users.json`.
- `/src/govops/api/app.py`(`create_app`, `/tasks`·`/approval`), `/src/govops/api/tasks.py`(`TaskService`, `_view`, `approve`), `/src/govops/api/sessions.py`(`SessionStore`), `/src/govops/agent/approval.py`(`approval_id`, `approver_decision`), `/src/govops/agent/users.py`(`load_users`), `/src/govops/agent/graph.py`(`build_graph`, `EffectDeps`, `WITH_WRITES`), `/tests/test_api_tasks.py`, `/tests/test_e2e_isolation.py`(in-process 조립 방식), 이전 step 산출물 `/src/govops/evalrun/observe.py`·`mock_admin.py`

## 작업

task를 **in-process ASGI로 실제 API 코드를 거쳐** 시작하고 **승인 스크립트(`approval_script`)**를 수행하는 드라이버를 만든다. TDD로 테스트를 먼저 쓴다.

### `src/govops/evalrun/driver.py`

```python
@dataclass(frozen=True) class DriveResult: task_id: str; view: dict; approvals: list[ApprovalRecord]; plan_remaining: int; unexpected_http: list[str]; timed_out: bool
class ApprovalDriver:
    def __init__(self, service: TaskService, users: Users, *, sleep=asyncio.sleep, poll_interval: float = 0.5, max_wait: float = 180.0): ...
    async def run(self, spec: TaskSpec) -> DriveResult
```

- 앱은 `create_app(users=frozenset(labels), sessions=SessionStore(), demo_login=False, service=service)`로 만들고 `httpx.AsyncClient(transport=httpx.ASGITransport(app))`로 호출한다(실제 서버 프로세스·네트워크 없음). 세션 토큰은 `SessionStore.issue(user)`로 직접 발급한다(`/sessions` 엔드포인트와 `DEMO_LOGIN`은 쓰지 않는다). 토큰은 요청 헤더에만 쓰고 결과·로그·예외에 남기지 않는다.
- 흐름: 요청자 세션으로 `POST /tasks` → `GET /tasks/{id}` 폴링으로 `awaiting_approval` 또는 종결 상태를 기다린다(`max_wait` 초과 시 `timed_out=True`로 중단). 승인 계획이 있고 `awaiting_approval`이면 단계마다 행위자 세션으로 `POST /tasks/{id}/approval`을 보낸다.
- **행위자 해석**: `requester` = 요청자 본인, `same_team_g4` = 요청자와 같은 팀이고 **4급인 사용자 중 요청자가 아닌 사용자** 중 `label` 사전순 첫 번째(없으면 `ValueError("no_approver")`), `other_team_user` = 요청자와 다른 팀의 사용자 중 `label` 사전순 첫 번째. 선택 기준은 코드 상수 주석과 문서(다음 step들)에 적는다.
- **결정 수행**: `approve` → `{"decision": "approve", "draft_sha256": <GET의 현재 해시>}`, `reject` → 같은 형식, `edit_approve` → `edited`로 초안 본문 필드(slack `text`·코멘트 `body`·후속 티켓 `description`) 끝에 줄바꿈 + `edit_append`를 붙인 값(수신처·`title`·`priority`는 보내지 않는다). 초안은 승인자가 볼 수 있는 `GET`(승인용 뷰)에서 읽는다(요청자는 `GET`으로 읽는다).
- **기록**: 호출마다 `ApprovalRecord(approval_id=approval_id(task_id, 보낸 해시), actor=<행위자 label>, decision, http_status=<실제 상태 코드>)`. 명세의 `expect_http`가 있으면 실제와 비교해 불일치를 `unexpected_http`에 `"<step>:<expected>!=<actual>"`로 적는다(`null`이면 2xx를 기대).
- 승인 후 종결(`completed`·`failed`·`rejected`·`expired`·`interrupted`)까지 폴링하고, 종결 뷰를 돌려준다. `plan_remaining`은 실행하지 못한 승인 단계 수다. 승인 계획이 없는데 `awaiting_approval`이 되면 더 기다리지 않고 그 상태로 반환한다(관측 어댑터가 `failed`로 매핑한다).

### 테스트 (가짜 chat·in-process mock·InMemory 저장소·InMemorySaver, 키·네트워크 불필요)

- 실제 `TaskService`·`build_graph(profile=WITH_WRITES)`를 가짜 chat(부작용 도구 호출을 내는 스크립트)으로 구동한다: T2형 승인 실행(효과 1), T6-01형 거부(효과 0), T6-03형 수정 승인(수정본 발송), T5-11형 차단(승인 계획 없음, 모델이 부작용 도구 호출 → 결정론 검사 차단), 전사 공지 승인자(`same_team_g4`) 성공과 **요청자 본인 승인은 403**, 다른 팀 사용자 승인은 404(`other_team_user`).
- 승인 계획 없이 승인 대기로 끝난 task → 폴링을 끝내고 `awaiting_approval` 반환, `plan_remaining` 계산.
- 타임아웃, `expect_http` 불일치 기록, 행위자 해석(결정론, 4급이 없으면 오류), 토큰이 결과에 없음.
- 모든 테스트는 가짜 `sleep`으로 실시간 대기가 없다.

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
   - 사용자 개입 필요 → `"status": "blocked"`, `"reason": "구체적 사유"` 후 즉시 중단

## 금지사항

- `/sessions` 엔드포인트·`DEMO_LOGIN`·네트워크 포트를 쓰지 마라. 이유: in-process ASGI + `SessionStore.issue`가 실제 API 코드를 거치는 가장 단순한 경로다. 실제 서버·세션 TTL은 거치지 않는다는 한계는 결과 문서에 적는다.
- 승인자 신원을 요청 본문에 넣지 마라. 이유: 승인자 신원은 세션에서만 정한다.
- 승인 단계에서 수신처를 바꾸는 `edited`를 보내지 마라. 이유: 승인자는 본문만 수정할 수 있다.
- 테스트에서 폴링 순서·응답 순서를 단언하지 마라(FP-012). 이유: 동시 실행의 순서는 스케줄링에 달려 있다.
- 무작위 task ID·`uuid`를 `monkeypatch`로 고정해 통과시키지 마라(FP-011).
- 폴링 대기를 실시간 `sleep`에 의존하게 만들지 마라.
- `scripts/`, `phases/`, `web/`, `data/contract/`, `data/eval/`을 수정하지 마라(이 step이 명시적으로 허용한 경우 제외).
- `.env`와 A의 `data/interim/mcp/tokens.json`을 열거나 출력하지 마라. 토큰·키 값을 코드·테스트·문서·로그에 남기지 마라.
- A 저장소(`~/PycharmProjects/ko-docs-agentic-rag`)를 수정하지 마라.
- 실제 LiteLLM·OpenAI·Anthropic·Gemini·A MCP·Langfuse·Docker에 접속하는 테스트를 만들지 마라. 이유: `make verify`는 키·네트워크 없이 통과해야 한다(외부 명령은 주입한 가짜 실행기로 검증한다).
- 기존 테스트를 깨뜨리지 마라. 계층 가드(`tests/test_architecture.py`)를 약화하지 마라.
