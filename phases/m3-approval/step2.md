# Step 2: checks-draft

## 읽어야 할 파일

- `/AGENTS.md`, `/docs/ADR.md` (ADR-005·006·008·011·012·017·018), `/docs/ARCHITECTURE.md` ("에이전트 상태 전이", "도구와 부작용·멱등성"), `/docs/THREAT_MODEL.md` (V5·V6·V9·V15)
- `/src/govops/agent/graph.py`, `/src/govops/agent/prompts.py`, `/src/govops/tools/registry.py`(`SendMessage`·`AddComment`·`CreateFollowup`, 도구 이름), `/src/govops/tools/markers.py`, `/src/govops/guardrails/pii.py`, `/src/govops/mocks/slack.py`(채널 목록), `/data/seed/users.json`, `/docs/ADR.md`(ADR-008 "팀별 정책")

## 작업

초안 타입과 **결정론 검사 중 수신처·PII·팀 정책** 부분을 만든다(발송 범위 V15는 다음 step). 모두 순수 Python이며 모델·네트워크를 쓰지 않는다. TDD로 테스트를 먼저 쓴다.

### `src/govops/agent/draft.py`

```python
@dataclass(frozen=True) class Draft: tool: str; args: Mapping[str, Any]      # 부작용 도구 3종만
def draft_sha256(draft: Draft) -> str                 # sha256(정규화 JSON{tool, args}), tools.markers의 정규화 규칙(NFC·키 정렬)과 같은 방식
def target_of(draft: Draft) -> str                    # slack: channel / 코멘트: ticket_id / 후속 티켓: parent_ticket_id
def body_text(draft: Draft) -> str                    # 검사·표시용: text / body / title+description 합친 문자열
```

### `src/govops/guardrails/policy.py` + 설정 파일

- 팀별 정책 표는 **설정 파일 하나**(`data/policy/team_policy.json`, 커밋)다. 로더 `load_team_policy(path) -> TeamPolicy`. 내용: HRM·FIN·LGL은 `pii: "block"`, 그 밖의 팀은 `pii: "mask"`. 알 수 없는 팀은 `block`(fail-closed). 정책 파일 sha256을 반환하는 함수도 둔다(서비스 시작 시 감사에 남기는 용도, 연결은 후속).
- `guardrails/`는 `govops`의 다른 계층을 import하지 않는다(순수 Python).

### `src/govops/agent/checks.py`

```python
@dataclass(frozen=True) class CheckResult: status: Literal["passed","masked","blocked"]; reason: str | None; draft: Draft; message: str | None
def check_recipient_and_pii(draft: Draft, *, requester: UserInfo, policy: TeamPolicy) -> CheckResult
```

- `UserInfo(label, team, grade)`는 `data/seed/users.json`에서 읽는 값 객체다. 사용자 목록 로더(`load_users(path)`)를 `agent/`에 둔다(경로는 인자, 기본은 `data/seed/users.json`).
- **수신 채널 허용 목록**: slack 초안의 채널은 `team-<요청자 팀>` 또는 `announce`만 허용한다. 그 밖(다른 팀 채널, 존재하지 않는 채널)은 `blocked`, 사유 `recipient_not_allowed`.
- **발송문 금지 PII**: `guardrails/pii`로 `body_text`를 검사한다. PII가 있으면 요청자 팀 정책이 `block`이면 `blocked`(`pii_blocked_by_team_policy`), `mask`면 **마스킹한 새 `Draft`**를 만들어 `masked`(`pii_masked`)로 돌려준다. 마스킹본은 원본과 다른 `draft_sha256`을 갖는다(승인자는 마스킹본을 본다). 재검사해도 PII가 남지 않아야 한다.
- 통과는 `passed`. `message`는 `blocked`일 때 쓰는 **고정 한국어 문구**(이유와 대안 안내: 보낼 수 있는 곳은 자기 팀 채널과 전사 공지임을 알린다)다. 문구에 PII·본문 원문·내부 코드를 넣지 않는다.
- `reason`은 코드 상수 목록으로 두고(`recipient_not_allowed`·`pii_blocked_by_team_policy`·`pii_masked`; 다음 step에서 `scope_wider_than_evidence`·`evidence_scope_unknown`·`ticket_not_found`가 추가된다), 감사 로그 허용 목록(`audit/log.py`의 이유 코드)에도 이 값들을 **추가**한다(기존 코드 변경 금지, 추가만).

### 테스트

- 채널 허용 목록: 자기 팀·전사 공지 통과, 다른 팀 채널·없는 채널 차단(평가 T5 `other_team_channel` 형태 포함).
- PII: 주민번호·전화·개인 이메일·계좌·카드번호 각각, 팀 정책 3팀(HRM·FIN·LGL) 차단, 나머지 팀 마스킹과 해시 변화, 마스킹 후 재검사 통과, 알 수 없는 팀은 차단.
- `draft_sha256` 안정성(키 순서·NFC 차이 무관), 인자가 다르면 해시가 다름. 사용자·팀·직급 필드가 인자에 있으면 `ValueError`.
- 계층 가드: `agent/`는 `broker`·`mocks`·`eval`·LLM SDK를 import하지 않는다. `tests/test_agent_data_boundary.py` 계속 통과.

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

- 검사 결과에 모델 판정을 섞지 마라. 이유: 실행 직전 결정론 검사는 탐지 모델에 의존하지 않는다.
- 차단 문구에 요청 본문·PII·내부 코드(영문 팀 코드 등)를 넣지 마라.
- 팀 정책을 코드 곳곳에 하드코딩하지 마라. 이유: 정책 표는 설정 파일 하나다.
- `scripts/`, `phases/`, `web/`, `data/contract/`, `data/eval/`을 수정하지 마라(이 step이 명시적으로 허용한 경우 제외).
- `.env`와 A의 `data/interim/mcp/tokens.json`을 열거나 출력하지 마라. 토큰·키 값을 코드·테스트·문서·로그에 남기지 마라.
- A 저장소(`~/PycharmProjects/ko-docs-agentic-rag`)를 수정하지 마라.
- 실제 LiteLLM·OpenAI·Anthropic·Gemini·A MCP·Langfuse에 접속하는 테스트를 만들지 마라. 이유: `make verify`는 키·네트워크 없이 통과해야 한다.
- 기존 테스트를 깨뜨리지 마라. 계층 가드(`tests/test_architecture.py`)를 약화하지 마라.
