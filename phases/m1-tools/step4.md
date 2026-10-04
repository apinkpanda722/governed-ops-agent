# Step 4: broker

## 읽어야 할 파일

먼저 아래 파일들을 읽고 프로젝트의 아키텍처와 설계 의도를 파악하라:

- `/AGENTS.md` (계층 경계 표의 `broker` 행, 아키텍처 규칙의 자격 증명 항목, 고정 제약의 토큰 항목)
- `/docs/ARCHITECTURE.md` ("자격 증명 중개 흐름 (`credential_for`)" 전체)
- `/docs/ADR.md` (ADR-005 전체: 불변식·데모 구현과 실무 설계의 차이)
- `/docs/THREAT_MODEL.md` (토큰 노출 관련 항목)
- step 1 산출물: `/src/govops/mocks/tokens.py`(평문 토큰 파일 형식 `{"users": [{"label","team","grade","tokens": {<audience>: <값>}}]}`)
- 참고(읽기 전용): `/Users/taserlee/PycharmProjects/ko-docs-agentic-rag/src/kodocs/mcp_server/tokens.py`의 `generate_tokens`가 만드는 A 평문 토큰 파일 형식 `{"users": [{"label","team","grade","token"}]}`. **실제 토큰 파일은 열지 마라. 코드만 읽는다.**
- `/src/govops/broker/__init__.py`, `/tests/test_architecture.py`, `/.env.example`

## 작업

자격 증명 중개 `broker`를 구현한다. 이 계층은 **A 토큰 파일(과 mock 토큰 파일)을 읽는 유일한 곳**이다. TDD로 테스트를 먼저 쓴다.

### 만들 파일: `src/govops/broker/credentials.py` (+ `__init__.py`에서 공개)

```python
Audience = Literal["kodocs-mcp", "mock-jira", "mock-slack"]

class CredentialError(Exception): ...
class UnknownUserError(CredentialError): ...
class UnknownAudienceError(CredentialError): ...
class CredentialSourceError(CredentialError): ...   # 파일 없음·형식 오류·환경 변수 없음

def credential_for(user_id: str, audience: Audience) -> str
    # 호출 시점에 토큰 파일을 읽어 해당 사용자·audience의 bearer 값("Bearer " 접두어 없이)을 돌려준다.
def known_users(audience: Audience) -> tuple[str, ...]
    # 해당 audience 토큰 파일에 있는 사용자 label을 오름차순으로 돌려준다(토큰 값은 돌려주지 않는다).
```

- `user_id`는 label(`demo-<team>-g<grade>`)이다.
- 토큰 출처(환경 변수 `os.environ`만 읽는다. `.env` 파일을 열지 마라):
  - `kodocs-mcp` → 환경 변수 `KODOCS_MCP_TOKENS_FILE`이 가리키는 A 형식 파일(`token` 필드).
  - `mock-jira`, `mock-slack` → 환경 변수 `MOCK_TOKENS_FILE`(없으면 기본 `data/interim/mock/tokens.json`)이 가리키는 파일의 `tokens[<audience>]`.
- `.env.example`에 `MOCK_TOKENS_FILE=`(값 없음, 위 기본값을 설명하는 주석 한 줄)을 추가한다. 형식은 기존 파일의 규칙(값 없는 대입)을 따른다.

핵심 규칙:
- **토큰은 호출 시점에 파일에서 읽고 모듈·프로세스 전역에 캐시하지 않는다.** 호출이 끝나면 호출자가 받은 반환 문자열 외에는 어디에도 남지 않는다. 같은 호출자가 파일을 바꾸면 다음 호출은 새 값을 돌려준다.
- 예외 메시지·`repr`·`str`·로그에 **토큰 값과 파일 내용을 넣지 않는다**. 메시지에는 user_id·audience 이름과 사유(없음/형식 오류)만 쓴다. 이 계층은 로깅을 하지 않는다.
- 알 수 없는 audience는 `UnknownAudienceError`, 파일에 없는 사용자는 `UnknownUserError`, 환경 변수가 없거나 파일이 없거나 JSON·필드 형식이 틀리면 `CredentialSourceError`다. 어느 경우에도 다른 사용자의 토큰을 대신 돌려주지 않는다.
- 이 모듈은 표준 라이브러리만 쓴다. 계층 가드(`tests/test_architecture.py`)의 `broker` 금지 import(agent, api, tools, gateway, obs, eval, langgraph)를 지킨다.

### 테스트 (`tests/test_broker.py`)

모든 파일은 `tmp_path`에 만들고 `monkeypatch.setenv`로 가리킨다. 실제 토큰 파일은 쓰지 않는다.
- A 형식 파일에서 `kodocs-mcp` 토큰, mock 형식 파일에서 `mock-jira`·`mock-slack` 토큰이 사용자별로 정확히 나온다. 같은 사용자의 세 audience 토큰은 서로 다르다.
- 알 수 없는 사용자·audience, 환경 변수 없음, 파일 없음, 잘못된 JSON, 필드 누락 각각이 올바른 예외 클래스를 낸다.
- **누출 검사**: 위 모든 예외의 `str`·`repr`·`args`와 `capsys`·`caplog` 출력에 파일 안의 어떤 토큰 값도 나타나지 않는다(테스트 파일에는 눈에 띄는 센티널 문자열 토큰을 쓰고 검색한다).
- 캐시 없음: 호출 사이에 파일을 다시 쓰면 새 토큰이 나온다.
- `known_users`가 label만(토큰 값 없이) 오름차순으로 돌려준다.
- 모듈 소스에 `logging`·`print`가 없다(간단한 소스 검사).
- `.env.example`이 기존 형식 검사(`tests/test_repo_rules.py`)를 계속 통과한다.

## Acceptance Criteria

```bash
make verify   # ruff check + ruff format --check + pytest + web 검사
```

## 검증 절차

1. 위 AC 커맨드를 실행한다.
2. 아키텍처 체크리스트를 확인한다:
   - 토큰 파일을 읽는 코드가 `broker` 한 곳뿐인가? 토큰이 캐시·로그·예외에 없는가?
   - `broker`가 금지된 계층을 import하지 않는가?
   - AGENTS.md CRITICAL 규칙을 위반하지 않았는가?
3. 결과를 최종 응답(JSON)으로 보고한다. `phases/` 아래 파일은 수정하지 않고 git commit도 하지 않는다:
   - 성공 → `"status": "completed"`, `"summary": "산출물 한 줄 요약"`
   - 해결할 수 없는 실패 → `"status": "error"`, `"reason": "구체적 에러 내용"`
   - 사용자 개입 필요 → `"status": "blocked"`, `"reason": "구체적 사유"` 후 즉시 중단

## 금지사항

- 토큰을 모듈 전역·`lru_cache`·클래스 속성에 캐시하지 마라. 이유: 토큰은 요청 직전에만 꺼내 쓰는 것이 불변식이다(ADR-005).
- `.env` 파일을 열거나 파싱하지 마라. 이유: `.env`는 사람·에이전트가 열지 않는다. 환경 변수는 프로세스 환경에서만 읽는다.
- 실제 `KODOCS_MCP_TOKENS_FILE`이 가리키는 A의 `data/interim/mcp/tokens.json`을 열거나 출력하지 마라. 테스트는 임시 파일만 쓴다.
- 이 계층에서 로깅하거나 출력하지 마라. 이유: 토큰 노출 경로를 없앤다.
- 토큰을 B 저장소 어디에도 복사해 두지 마라.
- docker, 설치·네트워크 명령을 실행하지 마라.
- A 저장소를 수정하지 마라. `scripts/`, `phases/`, `web/`, `data/contract/`를 수정하지 마라.
- 기존 테스트를 깨뜨리지 마라.
