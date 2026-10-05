# Step 4: approval-policy

## 읽어야 할 파일

- `/AGENTS.md`, `/docs/ADR.md` (ADR-005·006·008·011·012·017·018), `/docs/ARCHITECTURE.md` ("에이전트 상태 전이", "도구와 부작용·멱등성"), `/docs/THREAT_MODEL.md` (V5·V6·V9·V15)
- `/docs/ADR.md`(ADR-011, ADR-018 7항), `/docs/EVAL.md`(T6 승인 분기), `/data/eval/tasks.json`의 T6·T2 all_hands·T4 all_hands 항목(`approval_script`, `expect_http`; 읽기만)
- 이전 step 산출물: `/src/govops/agent/draft.py`, `/src/govops/agent/checks.py`, `/src/govops/agent/scope.py`

## 작업

**승인자 정책과 수정 규칙**을 순수 함수로 만든다. 화면·API는 이후다. TDD로 테스트를 먼저 쓴다.

### `src/govops/agent/approval.py`

```python
class Decision(StrEnum): ALLOW; FORBIDDEN; NOT_FOUND          # 403, 404에 대응
def approver_decision(draft: Draft, *, requester: UserInfo, approver: UserInfo | None) -> Decision
def apply_edit(draft: Draft, edited: Mapping[str, str]) -> Draft   # 위반 시 InvalidEdit
class InvalidEdit(Exception)
def approval_id(task_id: str, draft_sha256: str) -> str            # 결정론, 토큰·본문 없음
EDITABLE_FIELDS: Mapping[str, frozenset[str]]
```

### `approver_decision` 표 (그대로 구현한다)

| 작업 | 승인을 시도한 사용자 | 결과 |
|---|---|---|
| 전사 공지(`announce`) 발송 | 요청자 본인(직급 무관) | `FORBIDDEN`(403) |
| 전사 공지 발송 | 요청자와 같은 팀, 4급 이상, 요청자가 아님 | `ALLOW` |
| 전사 공지 발송 | 같은 팀 3급 이하 | `NOT_FOUND`(404) |
| 전사 공지 발송 | 다른 팀(직급 무관) | `NOT_FOUND` |
| 전사 공지 발송 | 알 수 없는 사용자(`approver is None`) | `NOT_FOUND` |
| 그 밖의 작업(팀 채널 발송·코멘트·후속 티켓) | 요청자 본인 | `ALLOW` |
| 그 밖의 작업 | 요청자 외 모두(같은 팀 4급 이상 포함) | `NOT_FOUND` |

- 403은 **전사 공지에서 요청자 본인에게만** 준다. 그 밖의 부적격 사용자는 모두 404다(task 존재를 알리지 않는다). 요청자가 4급 이상이어도 전사 공지 본인 승인은 403이다(예: 요청자 6급).
- 위 표의 모든 조합을 **파라미터 테스트**로 만든다(전사 공지/비공지 × 요청자 g2·g4·g6 × 승인자 요청자 본인·같은 팀 g3·g4·g5·다른 팀 g4·알 수 없음).
- 평가 명세의 T6 `other_user_approve`(다른 팀 사용자 승인 → 404)와 `all_hands_approver`(요청자 승인 → 403 뒤 같은 팀 4급 승인)를 테스트로 재현한다(`data/eval/tasks.json`은 읽기만, 값은 테스트에 상수로 옮기거나 읽어서 사용).

### 수정 규칙

- 수정 가능 필드: slack `text`, 코멘트 `body`, 후속 티켓 `title`·`description`. **수신처(`channel`)·`parent_ticket_id`·`ticket_id`·`priority`는 불변**이다. 수정 입력에 불변 필드나 알 수 없는 키가 있으면 `InvalidEdit`. 값은 문자열이고 비어 있지 않아야 하며(공백만 불가) 길이 상한(예: 4,000자)을 둔다.
- `apply_edit`은 새 `Draft`를 돌려준다(`draft_sha256`이 달라진다). 수정 후 결정론 검사(수신처·PII·팀 정책·V15)를 다시 돌리는 것은 그래프의 책임이다(다음 step). 이 함수는 검사를 하지 않는다.
- `approval_id`는 (task ID, 초안 해시)로만 만든다. 다른 해시에는 다른 ID가 나온다.

### 테스트

- 표 전체, 수정 가능·불가 필드, 수정 후 해시 변경과 불변 필드 보존, `InvalidEdit` 경로, `approval_id` 결정론.
- 계층 가드, `tests/test_agent_data_boundary.py` 통과.

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

- 승인자의 직급·팀을 요청 본문·도구 인자에서 받게 만들지 마라. 이유: 승인자 신원은 세션에서만 정한다.
- 전사 공지 외 작업에 403을 주지 마라. 이유: task 존재 비공개(404).
- 승인자가 수신처를 바꿀 수 있게 만들지 마라. 이유: 남의 이름으로 다른 채널에 보내는 경로가 생긴다(ADR-011).
- `scripts/`, `phases/`, `web/`, `data/contract/`, `data/eval/`을 수정하지 마라(이 step이 명시적으로 허용한 경우 제외).
- `.env`와 A의 `data/interim/mcp/tokens.json`을 열거나 출력하지 마라. 토큰·키 값을 코드·테스트·문서·로그에 남기지 마라.
- A 저장소(`~/PycharmProjects/ko-docs-agentic-rag`)를 수정하지 마라.
- 실제 LiteLLM·OpenAI·Anthropic·Gemini·A MCP·Langfuse에 접속하는 테스트를 만들지 마라. 이유: `make verify`는 키·네트워크 없이 통과해야 한다.
- 기존 테스트를 깨뜨리지 마라. 계층 가드(`tests/test_architecture.py`)를 약화하지 마라.
