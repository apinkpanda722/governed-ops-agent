# Step 0: agent-prompt

## 읽어야 할 파일

- `/AGENTS.md`(신원·프롬프트·승인 CRITICAL 규칙), `/docs/ADR.md`(ADR-011·ADR-018·ADR-021), `/docs/EVAL.md`(프롬프트 해시 항목)
- `/src/govops/agent/prompts.py`, `/src/govops/agent/org_info.py`, `/src/govops/agent/graph.py`(`AgentProfile`·`PROFILES`·`build_graph`·`run_task`·실행 노드·`AgentState`), `/src/govops/agent/checks.py`, `/src/govops/agent/approval.py`, `/src/govops/agent/users.py`, `/src/govops/agent/turns.py`(`turn_defaults`·`build_turn_state`·턴 범위 키 목록), `/src/govops/agent/scope.py`
- `/src/govops/replay/llm.py`(프롬프트 해시 계산 `prompt_hash`, 읽기 전용)
- `/tests/test_agent_turns.py`, `/tests/test_agent_graph.py`, `/tests/test_checks_draft.py`, `/tests/test_agent_org_info.py`, `/tests/test_golden_m3.py`, `/tests/test_golden_harness.py`, `/tests/test_golden_trace.py`
- `/.claude/skills/failure-patterns/SKILL.md`(FP-060·061) — 읽기만 한다

## 작업

**수정할 파일**(이 목록이 전부다): `src/govops/agent/prompts.py`, `src/govops/agent/requester.py`(신규), `src/govops/agent/org_info.py`, `src/govops/agent/checks.py`, `src/govops/agent/approval.py`, `src/govops/agent/graph.py`, `src/govops/agent/turns.py`, `tests/test_agent_requester_prompt.py`(신규), `tests/test_agent_turns.py`·`tests/test_agent_graph.py`·`tests/test_checks_draft.py`(새 이름·새 프로필·새 문구에 따른 추가·갱신에 한함, 단언 약화 금지). 계층: `agent/`는 `broker`·`mocks`·`eval`·LLM SDK를 import할 수 없다. TDD.

열람 금지 경로: `data/eval/`, `data/interim/`
수정 허용 경로: `src/govops/agent/prompts.py`, `src/govops/agent/requester.py`, `src/govops/agent/org_info.py`, `src/govops/agent/checks.py`, `src/govops/agent/approval.py`, `src/govops/agent/graph.py`, `src/govops/agent/turns.py`, `tests/test_agent_requester_prompt.py`, `tests/test_agent_turns.py`, `tests/test_agent_graph.py`, `tests/test_checks_draft.py`

열람 금지의 범위: 이 금지는 **Codex 세션이 `data/eval/`·`data/interim/`의 파일을 직접 열람·출력·인용하는 것**에 대한 것이다. 기존 코드·테스트가 런타임에 두 경로를 읽는 것은 대상이 아니다. 이 step의 코드와 새 테스트는 두 경로를 읽지 않는다.

**배경**: 데모에서 "팀 채널에 공유해줘"라고 하면 모델이 어느 팀 채널인지 되묻거나, 허용 목록 밖 채널로 초안을 내서 수신처 검사에 막혔다. 모델이 요청자의 소속 팀과 보낼 수 있는 채널을 알도록 시스템 프롬프트에 **요청자 절**을 넣는다. 프롬프트가 바뀌면 커밋된 골든 14개의 `prompt_hash`가 어긋나 CI가 "재기록 필요"로 실패하므로, 기존 프로필 `writes_org`는 **그대로 두고** 새 프로필 `writes_team`을 옆에 둔다(ADR-021 선례). 골든 재기록은 이 phase에서 하지 않는다(사용자 dev 재실행 때 러너가 한다).

### 1. 채널 규칙의 단일 출처 (`checks.py`·`approval.py`)

- `checks.allowed_channels(team: str) -> tuple[str, ...]`: 팀이 `_CHANNEL_TEAMS`에 있으면 `("team-<team>", "announce")`, 없으면 `("announce",)`. `check_recipient_and_pii`는 이 함수로 수신처를 판정한다(기존 판정 결과는 그대로여야 한다).
- `checks.recipient_not_allowed_message(team: str) -> str`: `"허용되지 않은 수신처라 보낼 수 없습니다. 자기 팀 채널(<team-채널 ID>)과 전사 공지(announce)로만 보낼 수 있습니다."` 형태. 팀 채널이 없는 팀은 전사 공지만 안내한다. 본인 팀 정보만 넣고 다른 팀 채널 이름은 넣지 않는다. 기존 테스트가 `"자기 팀 채널"`과 `"전사 공지"` 포함을 단언하므로 두 표현은 유지한다. 개인정보 차단 문구는 바꾸지 않는다.
- `approval.ANNOUNCE_APPROVER_MIN_GRADE = 4`를 상수로 올리고 `approver_decision`이 이 상수를 쓴다(동작 불변).

### 2. 요청자 절 (`requester.py`)

- `requester_section(user: UserInfo) -> str`: 결정론 문자열. 내용은 소속 팀(한글 이름과 `owner_team`, `org_info`의 표에서 찾는 `team_name_ko(owner_team)` 헬퍼를 `org_info.py`에 둔다), 보낼 수 있는 Slack 채널(`allowed_channels` 결과, 각각 "소속 팀 채널"·"전사 공지" 설명), 전사 공지 승인 조건(요청자 본인은 승인할 수 없고 같은 팀 `ANNOUNCE_APPROVER_MIN_GRADE`급 이상 사용자가 승인한다). **직급·사용자 라벨·다른 팀 이름·채널 ID·정답 값·문서 ID·티켓 ID는 넣지 않는다.**
- `graph.system_prompt_for(graph, user_id: str) -> str`: 프로필의 `requester_section`이 거짓이면 `profile.system_prompt` 그대로, 참이면 `profile.system_prompt + "\n" + requester_section(users[user_id])`. 사용자가 `users`에 없으면 `ValueError("unknown_user")`(fail-closed). 팀·직급은 인증된 세션의 `user_id`로 `users` 표에서만 얻는다.
- `run_task`는 시스템 프롬프트로 `system_prompt_for(...)`를 쓴다. `build_graph`가 받은 `users`를 `graph.agent_users`로 보관한다. (`api/tasks.py`가 같은 함수를 쓰도록 바꾸는 일은 다음 api step의 몫이다. 이 step은 `api/`를 수정하지 않는다.)

### 3. 새 프로필 `writes_team`

- `AgentProfile`에 필드 `requester_section: bool = False`를 추가하고 기존 프로필(`READ_ONLY`·`WITH_WRITES`·`WITH_WRITES_ORG`·`writes_org`)은 값·프롬프트·도구를 바꾸지 않는다. **`writes_org`의 `system_prompt`와 프롬프트 해시(`0a82447c0360f47db5f433d2d9e8b56e70a70c8c95f37479b86d8d6af95cdda7`)는 불변이다.**
- 템플릿(`writes_team.system_prompt`, 사용자와 무관한 정적 문자열) = `WITH_WRITES_ORG`의 프롬프트에서 문장 "수신처는 사용자가 요청한 곳만 사용합니다."를 `prompts.py`의 `SEND_RULES`로 바꾼 것. `SEND_RULES`의 뜻: Slack 수신처 `channel`은 아래 "요청자 정보"의 채널 중에서만 고른다 / 사용자가 "팀 채널"이라고만 하면 되묻지 말고 요청자 소속 팀 채널로 초안을 만든다 / "전사 공지"를 명시했을 때만 `announce`를 쓴다 / 목록에 없는 채널을 요청받으면 초안을 만들지 말고 보낼 수 있는 채널을 안내한다. `PROFILES`에 `"writes_team"`을 추가한다(레이블 `writes_team`, `requester_section=True`, 도구는 `writes_org`와 같다).
- `profile_for`가 새 레이블을 돌려준다. **`GOVOPS_AGENT_PROFILE` 기본값과 `api/wiring.py`는 이 step에서 바꾸지 않는다.**
- `writes_org`가 새 기본값으로 쓰이지 않는 동결 프로필임을 `PROFILES` 위 주석 한 줄로 적는다.

### 4. 효과 실행 시각 `effect_at`

- 실행 노드가 효과를 실행했을 때 상태에 `effect_at`(실행 시점의 UTC ISO 문자열, 예: `datetime.now(UTC)`를 노드의 `clock`으로 얻은 값에 `astimezone(UTC).isoformat()`)를 **`effect` 딕셔너리 밖의 별도 키**로 돌려준다. `AgentState`에 키를 추가하고 `turn_defaults()`에 `effect_at: None`을 넣어 매 턴 초기화한다. 턴 범위 키 목록(`turns.py`)에도 이름만 추가한다.

### 5. 테스트

- `tests/test_agent_requester_prompt.py`(신규):
  - 시드 사용자 **전원**에 대해 `requester_section`이 소속 팀 한글 이름·`allowed_channels` 채널을 담고, 직급 숫자·사용자 라벨·다른 팀의 한글 이름과 채널 ID·`syn-` 접두 문서 ID·티켓 키 형식(`[A-Z]{3}-\d+`)이 없다(사용자 수가 1 이상임을 먼저 단언, FP-069).
  - `allowed_channels`가 가리키는 채널로 만든 초안은 `check_recipient_and_pii`를 통과하고 다른 팀 채널은 `recipient_not_allowed`로 막힌다(시드 전 팀). 프롬프트가 안내하는 채널 집합과 검사가 허용하는 집합이 같다.
  - `ANNOUNCE_APPROVER_MIN_GRADE`가 `approver_decision`과 일치한다(그 급 미만은 거절, 이상은 허용 — 같은 팀·본인 제외 조건 포함)하고 요청자 절이 같은 숫자를 말한다.
  - 템플릿은 사용자와 무관하다(두 사용자의 `system_prompt_for` 결과에서 요청자 절을 뺀 앞부분이 같고, 요청자 절은 다르다). `writes_org`의 `prompt_hash`가 위 고정 해시와 같고 `writes_team`의 해시는 다르다(`replay.llm.prompt_hash`로 계산, 도구·모델·파라미터는 골든과 같은 값).
  - 실제 그래프 경로: 가짜 챗이 받은 첫 messages의 시스템 메시지가 `system_prompt_for(...)`와 같고, 다른 사용자로 시작한 대화는 다른 요청자 절을 받는다. `unknown_user`는 모델 호출 전에 실패한다.
  - `effect_at`: 승인 뒤 효과가 실행된 턴의 상태에 파싱 가능한 UTC ISO 문자열이 있고 `effect` 안에는 키가 없다. 효과가 없는 턴(거부·읽기 전용)에는 `None`이다. 다음 턴이 시작되면 초기화된다.
  - 새 문구: `recipient_not_allowed_message`가 본인 팀 채널만 담고 다른 팀 채널 ID는 담지 않는다.
- 기존 테스트: `test_agent_turns.py`의 턴 범위 키 집합 단언에는 **`effect_at` 이름만 추가**한다. `test_agent_graph.py`·`test_checks_draft.py`는 새 프로필·새 문구에 필요한 부분만 고친다(문구 단언은 위 두 표현을 유지).

## Acceptance Criteria

```bash
make verify   # ruff check + ruff format --check + pytest + web 검사
git diff --exit-code -- data/replay/golden data/replay/llm   # 골든 14개 불변(재기록 금지)
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

- `writes_org`의 프롬프트·도구 정의·해시를 바꾸거나 골든 파일(`data/replay/golden`·`data/replay/llm`)을 다시 기록하지 마라. 이유: 커밋된 골든의 `prompt_hash`가 달라지면 CI가 "재기록 필요"로 실패한다. 재기록은 사용자 dev 재실행 때 러너가 한다(ADR-021).
- 팀·채널을 사용자 입력·모델 출력·요청 본문에서 얻지 마라. 요청자 절은 세션 사용자 ID로 `users` 표를 조회한 값만 쓴다. 이유: 신원은 인증된 세션에서만 정한다(AGENTS.md CRITICAL). 프롬프트에 직급·라벨·다른 팀 정보·정답 값·문서 ID를 넣지 마라(권한 정보 최소화와 평가 오염 방지).
- 채널 목록이나 승인 급수를 프롬프트 코드에 따로 복사해 두지 마라. 이유: 프롬프트·결정론 검사·승인 정책이 어긋나면 모델이 허용되지 않는 채널을 안내한다. `allowed_channels`와 `ANNOUNCE_APPROVER_MIN_GRADE`가 단일 출처이고 일치 테스트가 막는다(FP-060).
- `effect_at`을 `effect` 딕셔너리 안에 넣거나 턴 초기화를 빼먹지 마라. 이유: `effect`의 모양을 정확히 단언하는 골든·테스트가 있고, 초기화를 빼먹으면 이전 턴의 시각이 다음 턴에 남는다(FP-060·061).
- `api/`·`evalrun/`·`web/`을 수정하거나 `GOVOPS_AGENT_PROFILE` 기본값을 바꾸지 마라. 이유: 기본값 전환·`TaskService` 연결은 api step, 러너 전환은 등록 step의 범위다. 이 step에서는 `writes_team`을 아무도 기본으로 쓰지 않는다.
