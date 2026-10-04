# Step 3: checkpointer-db

## 읽어야 할 파일

먼저 아래 파일들을 읽고 프로젝트의 아키텍처와 설계 의도를 파악하라:

- `/AGENTS.md` (기술 스택의 LangGraph·checkpointer 항목, 계층 경계 표의 `agent` 행)
- `/docs/ARCHITECTURE.md` ("상태 관리"의 에이전트 상태, "배치도" 설명), `/docs/ADR.md` (ADR-011, ADR-015, ADR-016, ADR-017의 checkpointer DB 결정)
- `/infra/postgres/init/01-create-databases.sh`, `/infra/docker-compose.yml`, `/tests/test_compose.py`, `/.env.example`(`GOVOPS_DATABASE_URL` 이름과 값 형식 주석), `/Makefile`, `/tests/test_makefile.py`
- `/src/govops/agent/__init__.py`, `/tests/test_architecture.py`
- 설치된 `langgraph`·`langgraph-checkpoint`·`langgraph-checkpoint-postgres`의 `InMemorySaver`·`AsyncPostgresSaver` API(`.venv`의 `site-packages`, 읽기만)

## 작업

checkpointer DB `govops`를 만들고 checkpointer 팩토리를 구현한다. `make verify`는 Postgres 없이 통과해야 하므로 **테스트는 in-memory saver만** 쓰고 실제 Postgres 접속은 사용자의 스모크로 확인한다(다음 phase 마지막 step의 `smoke-checkpointer`). TDD로 테스트를 먼저 쓴다.

### 설계 (ADR-017 확정)

- 같은 Postgres 인스턴스에 새 DB `govops`를 둔다(앱 DB). checkpointer 테이블은 LangGraph가 `setup()`으로 만든다. M3 outbox 저장 위치는 여기서 정하지 않는다.
- 호스트의 에이전트가 접속하므로 연결 주소는 `127.0.0.1:5432`다(`GOVOPS_DATABASE_URL`, 값 형식은 `.env.example` 주석 참고. 컨테이너용 `LITELLM_DATABASE_URL`의 호스트 `postgres`와 다르다).

### 만들 것

1. **`infra/postgres/init/01-create-databases.sh`**: `CREATE DATABASE govops;`를 추가한다(`litellm`·`langfuse`는 그대로). init 스크립트는 빈 볼륨에서만 실행되므로 기존 볼륨용으로 아래 `make db-init`을 둔다.
2. **`Makefile`의 `db-init` 타깃**(멱등): 실행 중인 compose의 postgres 컨테이너에 `docker compose -f infra/docker-compose.yml exec -T postgres ...`로 접속해 `govops` DB가 없을 때만 `CREATE DATABASE govops`를 실행한다(있으면 아무것도 하지 않고 성공). 위에 한 줄 주석("compose 기동 후 실행, 이미 있으면 변경 없음"). `verify`는 이 타깃을 호출하지 않는다.
3. **`src/govops/agent/checkpointer.py`**
   ```python
   @asynccontextmanager
   async def open_checkpointer(url: str | None) -> AsyncIterator[BaseCheckpointSaver]
   ```
   - `url`이 `None`·빈 문자열·`"memory"`이면 `InMemorySaver`를 돌려준다.
   - 그 밖의 값은 `AsyncPostgresSaver.from_conn_string(url)`로 열고 `await saver.setup()` 후 돌려주며, 컨텍스트 종료 시 연결을 닫는다.
   - 연결 문자열(비밀번호 포함)을 로그·예외 메시지·`repr`에 남기지 않는다. 연결 실패는 "checkpointer DB 연결 실패" 같은 일반 메시지의 예외로 바꿔 올린다(원인 예외 체인에 URL이 있어도 `str()`에는 나타나지 않게).
4. **`tests/test_compose.py` 확장**: init 스크립트가 `litellm`·`langfuse`·`govops` 세 DB를 만든다는 검사. `tests/test_makefile.py` 확장: `db-init` 타깃이 있고 `CREATE DATABASE govops`를 포함하며 `verify`가 호출하지 않는다.
5. `.env.example`의 `GOVOPS_DATABASE_URL`은 이미 있다. 이름이 바뀌면 안 된다.

### 테스트 (`tests/test_checkpointer.py`)

- `open_checkpointer(None)`·`""`·`"memory"`가 `InMemorySaver`를 준다. 이 saver로 작은 LangGraph 그래프(노드 1개)를 실행해 체크포인트를 쓰고 `aget_state`로 같은 값을 읽는다(왕복).
- Postgres 경로: `AsyncPostgresSaver.from_conn_string`을 `monkeypatch`로 가짜 비동기 컨텍스트 매니저로 바꿔 (a) 전달된 URL이 그대로인지 (b) `setup()`이 `await`되는지 (c) 컨텍스트 종료 시 닫히는지를 확인한다. 실제 DB 접속은 하지 않는다.
- 연결 실패를 흉내 낸 가짜가 예외를 던질 때, 올라오는 예외의 `str`·`repr`에 비밀번호 센티널이 든 URL이 없다.
- 계층 가드 통과(`agent`는 broker·mocks·eval·LLM SDK를 import하지 않는다. `langgraph`·`psycopg`는 허용).

## Acceptance Criteria

```bash
make verify   # ruff check + ruff format --check + pytest + web 검사
```

## 검증 절차

1. 위 AC 커맨드를 실행한다.
2. 아키텍처 체크리스트를 확인한다:
   - `govops` DB가 init 스크립트와 `make db-init` 양쪽에 있고, 기존 두 DB 초기화가 보존되는가?
   - 테스트가 Postgres 없이 통과하는가? 연결 문자열이 로그·예외에 없는가?
   - AGENTS.md CRITICAL 규칙을 위반하지 않았는가?
3. 결과를 최종 응답(JSON)으로 보고한다. `phases/` 아래 파일은 수정하지 않고 git commit도 하지 않는다:
   - 성공 → `"status": "completed"`, `"summary": "산출물 한 줄 요약"`
   - 해결할 수 없는 실패 → `"status": "error"`, `"reason": "구체적 에러 내용"`
   - 사용자 개입 필요 → `"status": "blocked"`, `"reason": "구체적 사유"` 후 즉시 중단

## 금지사항

- docker, `docker compose`, `make db-init`을 실행하지 마라. 이유: DB 생성은 사용자가 한다.
- 실제 Postgres에 접속하는 테스트를 만들지 마라. 이유: `make verify`는 네트워크·DB 없이 통과해야 한다.
- 연결 문자열·비밀번호를 코드·로그·예외·테스트 출력에 남기지 마라.
- outbox 테이블이나 outbox 저장 위치를 만들지 마라. 이유: M3에서 정한다.
- checkpointer 보존 기간 삭제(ADR-015)를 구현하지 마라. 이유: M5 범위다.
- `.env`와 A의 `data/interim/mcp/tokens.json`을 열거나 출력하지 마라. A 저장소를 수정하지 마라.
- `scripts/`, `phases/`, `web/`, `data/contract/`를 수정하지 마라.
- 기존 테스트를 깨뜨리지 마라.
