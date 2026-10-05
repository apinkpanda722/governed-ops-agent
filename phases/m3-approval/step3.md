# Step 3: scope-v15

## 읽어야 할 파일

- `/AGENTS.md`, `/docs/ADR.md` (ADR-005·006·008·011·012·017·018), `/docs/ARCHITECTURE.md` ("에이전트 상태 전이", "도구와 부작용·멱등성"), `/docs/THREAT_MODEL.md` (V5·V6·V9·V15)
- `/src/govops/eval/audience.py`, `/src/govops/eval/visibility.py`(`can_read`), `/docs/EVAL.md`("발송 범위 규칙"), `/docs/ADR.md`(ADR-008 발송 범위 규칙, ADR-018 8항)
- 이전 step 산출물: `/data/seed/access_index.json`, `/src/govops/agent/draft.py`, `/src/govops/agent/checks.py`, `/src/govops/guardrails/policy.py`, `/tests/test_agent_data_boundary.py`
- 도구 결과 형태 확인: `/data/replay/a_run/T1-01.jsonl`(`lookup_id`도 `text`를 돌려준다), `/data/contract/*.v1.json`, `/src/govops/tools/registry.py`, `/src/govops/mocks/jira.py`(응답 형태)

## 작업

**V15 발송 범위 검사**를 에이전트 안에 구현한다: 보낼 곳의 독자 범위가 근거의 열람 범위보다 넓으면 차단한다. TDD로 테스트를 먼저 쓴다.

### `src/govops/agent/scope.py`

```python
class ScopeIndex:   # data/seed/access_index.json + users.json 로드. data/eval을 열지 않는다
    @classmethod def load(cls, index_path=None, users_path=None) -> "ScopeIndex"
    def readers(self, draft: Draft) -> list[UserInfo]             # 보낼 곳의 독자
    def doc_row(self, doc_id) -> AccessRow | None
    def ticket_row(self, ticket_id) -> AccessRow | None
def can_read(user: UserInfo, row: AccessRow) -> bool              # eval.visibility.can_read와 같은 규칙(재구현, eval import 금지)
def evidence_ids(tool: str, payload: Mapping) -> set[str]         # 근거 추출
def check_scope(draft: Draft, evidence: Collection[str], *, requester: UserInfo, scope: ScopeIndex) -> CheckResult
def run_checks(draft, *, evidence, requester, policy, scope) -> CheckResult   # check_recipient_and_pii → check_scope 합성
```

- **근거(evidence) 정의 = 구조 규칙**: 도구 결과에서 ① 비어 있지 않은 `text`를 가진 항목의 `doc_id`(A의 `search_docs` 발췌, `get_document`, `lookup_id` 모두 해당) ② 레코드가 돌아온 티켓 ID(`jira_get_ticket`·`jira_search_tickets`). `run_sql` 결과는 근거가 없다. **`lookup_id`도 본문(`text`)을 돌려주므로 근거에 포함한다**(`data/replay/a_run/T1-01.jsonl`의 `syn-meeting-001`이 근거에 들어가는 테스트를 둔다).
- **독자**: 팀 채널(`team-<팀>`)은 그 팀 전 직급, `announce`는 전 직원, 코멘트·후속 티켓은 그 티켓(후속 티켓은 원 티켓)을 읽을 수 있는 사람이다(후속 티켓은 원 티켓의 열람 범위를 이어받는다).
- **규칙**: 모든 근거에 대해 독자 전원이 그 근거를 읽을 수 있어야 통과한다. 하나라도 넓으면 `blocked`, 사유 `scope_wider_than_evidence`. 인덱스에 없는 근거(비합성 문서 등)는 `blocked`, 사유 `evidence_scope_unknown`(fail-closed). 근거가 하나도 없는 초안은 이 검사를 통과한다(집계 보고 T4처럼 근거 문서가 없는 경우).
- **쓰기 존재 비공개**: 코멘트·후속 티켓의 대상 티켓이 인덱스에 없거나 **요청자가 읽을 수 없으면** 사유 `ticket_not_found`로 차단하고 안내 문구는 **존재하지 않는 티켓과 똑같은 "찾을 수 없음" 문구**를 쓴다(존재를 알리지 않는다). 사유 코드 자체는 감사 로그에만 쓴다.
- 차단 안내 문구는 고정 한국어 템플릿이다: `scope_wider_than_evidence`는 "보낼 곳의 독자 범위가 근거 문서의 열람 범위보다 넓어 발송할 수 없습니다" + 대안(근거의 열람 범위에 맞는 곳으로 요청), `evidence_scope_unknown`은 "근거의 열람 범위를 확인할 수 없어 발송할 수 없습니다". 이 두 사유 코드를 `audit/log.py` 이유 코드 허용 목록에도 **추가**한다.
- `evidence_ids`는 도구 결과를 한 번 훑는 순수 함수다. 에이전트 상태에 근거를 누적하는 연결은 이후 step에서 한다.

### 테스트

- V15 표 사례: 팀 전용 문서→전사 공지 차단, 직급 제한 문서→팀 채널 차단, 다른 팀 문서→요청자 팀 채널 차단, 공개 문서→전사 공지 통과, 팀 전용 문서→그 팀 채널 통과, 근거 없음 통과, 인덱스에 없는 문서 차단.
- 근거 추출: `lookup_id`·`get_document`·`search_docs`·티켓 조회 결과의 구조 규칙(빈 `text`는 제외), `run_sql`은 근거 없음. `a_run/T1-01.jsonl`에서 `syn-meeting-001`·`syn-incident-014`가 근거로 나온다.
- **일관성 테스트**: 모든 사용자 × 모든 문서·티켓 조합에서 `agent.scope.can_read`와 `eval.visibility.can_read`가 같다. 팀 채널·전사 공지 독자에 대한 문서 단위 판정이 `eval.audience.audience_covered`와 같다(테스트 파일은 `eval`을 import해도 된다).
- 후속 티켓·코멘트: 요청자가 읽을 수 없는 티켓과 없는 티켓이 같은 `ticket_not_found`·같은 안내 문구다.
- `tests/test_agent_data_boundary.py` 계속 통과.

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

- `agent/scope.py`가 `govops.eval`을 import하거나 `data/eval/`을 읽지 마라. 이유: `meta.json`에는 facts·canaries·injections가 있다.
- 근거를 모델이 선언하게(도구 인자로 받게) 만들지 마라. 이유: 인젝션이 근거를 비워 검사를 피할 수 있다. 근거는 도구 결과에서 구조로 추출한다.
- 인덱스에 없는 근거를 통과시키지 마라. 이유: fail-closed다.
- 존재하지 않는 티켓과 읽을 수 없는 티켓의 안내 문구를 다르게 만들지 마라. 이유: 존재 비공개(ADR-017).
- ADR 문서는 이 step에서 고치지 마라(마지막 step이 한다).
- `scripts/`, `phases/`, `web/`, `data/contract/`, `data/eval/`을 수정하지 마라(이 step이 명시적으로 허용한 경우 제외).
- `.env`와 A의 `data/interim/mcp/tokens.json`을 열거나 출력하지 마라. 토큰·키 값을 코드·테스트·문서·로그에 남기지 마라.
- A 저장소(`~/PycharmProjects/ko-docs-agentic-rag`)를 수정하지 마라.
- 실제 LiteLLM·OpenAI·Anthropic·Gemini·A MCP·Langfuse에 접속하는 테스트를 만들지 마라. 이유: `make verify`는 키·네트워크 없이 통과해야 한다.
- 기존 테스트를 깨뜨리지 마라. 계층 가드(`tests/test_architecture.py`)를 약화하지 마라.
