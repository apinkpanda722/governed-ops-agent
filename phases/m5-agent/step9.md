# Step 9: conversation-smoke-pg

## 읽어야 할 파일

- `/AGENTS.md`(`## 명령어` 절 표기 형식, CRITICAL 규칙), `/docs/ADR.md`(ADR-013·ADR-015의 2026-10-08 변경 기록), `/docs/PRD.md`(M5 행)
- 이전 step 산출물: `/src/govops/api/conversations.py`(`PostgresTurnIndex`), `/src/govops/api/retention.py`(`run_prune`), `/src/govops/agent/retention.py`, `/src/govops/api/app.py`(라우트 계약), `/src/govops/api/tasks.py`
- `/src/govops/api/cli.py`(HTTP 클라이언트 형식: 데모 로그인, `transport` 주입, 세션 토큰을 저장하지 않음), `/src/govops/api/smoke.py`(`smoke_agent`의 질문 `REQUEST`와 기대값), `/src/govops/evalrun/dev_m3.py`의 `smoke_specs()`(승인 스모크가 쓰는 요청 문장), `/data/seed/users.json`(사용자 label: `demo-platform-g3`, `demo-finance-g3` 등)
- `/Makefile`(`serve`·`ask`·`smoke-agent`·`smoke-approval`·`test-outbox-pg` 레시피 형식), `/tests/test_makefile.py`, `/tests/test_outbox_pg.py`(임시 테이블·`GOVOPS_TEST_DATABASE_URL` 건너뛰기 방식), `/tests/test_checkpointer.py`
- `/.claude/skills/failure-patterns/SKILL.md`(FP-020·031·040·050·051·052·066) — 읽기만 한다
- **외부 계약 실측**(2026-10-08):

| 지점 | 성공 여부 | 반환 내용 |
|---|---|---|
| `GOVOPS_TEST_DATABASE_URL`의 DB | 사용 가능 | `.env.example` 주석대로 `govops` DB를 그대로 가리켜도 되는 URL이다. 그러나 checkpointer 테이블 이름은 고정(`checkpoints`·`checkpoint_blobs`·`checkpoint_writes`)이라 **그 DB에서 삭제 테스트를 하면 실제 대화를 지울 수 있다**. 그래서 삭제 테스트는 임시 DB를 만들어 그 안에서만 한다 |
| `AsyncPostgresSaver.adelete_thread` | 성공 | 3개 테이블에서 그 thread만 지운다(step 3 표) |
| 스모크가 서비스에 접속하는 방법 | 동작 | `make serve`가 `DEMO_LOGIN=true`로 127.0.0.1:8000에서 `POST /sessions`를 연다. 스모크는 `cli.py`처럼 HTTP로만 접속하며 모듈 안에서 `broker`·`mocks`·`evalrun`을 import하지 않는다 |

## 작업

**수정할 파일**(이 목록이 전부다): `src/govops/api/smoke_conversation.py`(신규), `tests/test_api_smoke_conversation.py`(신규), `tests/test_m5_pg.py`(신규), `Makefile`, `AGENTS.md`(`## 명령어` 절에 `make smoke-conversation`·`make test-m5-pg` 두 줄), `tests/test_makefile.py`(새 타깃 테스트 추가에 한함, 기존 단언 유지). 계층: `api/`는 `broker`·`mocks`·LLM SDK를 import할 수 없고 `evalrun`을 import할 수 없다. TDD.

열람 금지 경로: `data/eval/`, `data/interim/`
수정 허용 경로: `src/govops/api/smoke_conversation.py`, `tests/test_api_smoke_conversation.py`, `tests/test_m5_pg.py`, `Makefile`, `AGENTS.md`, `tests/test_makefile.py`

열람 금지의 범위: 이 금지는 **Codex 세션이 `data/eval/`·`data/interim/`의 파일을 직접 열람·출력·인용하는 것**에 대한 것이다. 스모크는 HTTP로만 서비스에 접속하고 이 경로를 읽지 않는다. 테스트는 두 경로의 실제 파일 내용을 읽지 않는다.

**배경**: M5 데모 스모크(`make smoke-conversation`)와 실제 Postgres 검증(`make test-m5-pg`)은 둘 다 **사용자 실행**이다. 하네스 step에서는 만들기만 하고 실행하지 않는다. 스모크의 검사 논리는 스텁 서버에 대한 계약 테스트로 `make verify` 안에서 검증한다.

### 1. `api/smoke_conversation.py` (HTTP 클라이언트, `python -m govops.api.smoke_conversation [--base-url http://127.0.0.1:8000] [--timeout 180]`)

`cli.py`처럼 `transport` 주입을 받고 세션 토큰을 저장·출력하지 않는다. 출력은 줄마다 `PASS 이름`, `FAIL 이름 사유코드`, `INFO 이름 값`이고(답변·요청 원문·토큰을 출력하지 않는다. mock ID와 상태 값은 허용), 마지막에 `RESULT pass|fail`이다. `FAIL`이 하나라도 있으면 종료 코드 1이다. 모든 대기에 마감이 있다(FP-066). 시나리오(`demo-platform-g3`=플랫폼팀, `demo-finance-g3`=재무팀):

1. 플랫폼팀: `POST /conversations`로 `smoke.py`의 `REQUEST` 질문을 보내 `GET /conversations/{id}`가 `completed`·답변 있음·`observed_doc_ids` 있음 → `PASS rag_turn1`.
2. 같은 대화 2번째 턴: `smoke_specs()`의 승인 스모크 요청 문장 형식을 따라 "팀 채널에 공유" 요청을 보낸다. `awaiting_approval`과 초안이 나오면 `PASS share_draft`, 마감 안에 초안이 없으면 `FAIL share_draft draft_missing`(모델 동작 의존이라 사용자가 재실행한다).
3. 승인 대기 중: 새 턴 → 409 `approval_pending`이면 `PASS approval_pending_409`. 승인함 `GET /approvals`에 그 턴이 있으면 `PASS inbox_lists_pending`. 재무팀 사용자가 그 대화를 `GET`하면 404 `PASS other_user_404`이고 재무팀의 승인함에는 그 턴이 없으면 `PASS inbox_hides_from_others`.
4. 승인: `POST /tasks/{turn_id}/approval`(`approve`, `draft_sha256`) → `completed`와 `effect`의 mock ID → `PASS approve_sends_once`. 같은 승인을 한 번 더 보내 200이고 같은 `effect`(같은 mock ID)이면 `PASS duplicate_approval_idempotent`.
5. 같은 대화 3번째 턴(쓰기 요청 하나 더): 시작 직후 이전 턴의 초안·효과를 물려받지 않는지(`draft_sha256`이 2번째 턴과 다름, `effect` 없음)와, 승인하면 **2번째 턴과 다른 mock ID의 효과 1건**만 생기는지 확인 → `PASS second_write_separate_effect`. 3번째 턴 뒤 2번째 턴의 `turn_id`로 승인을 다시 보내 404이면 `PASS late_duplicate_404`, 첫 턴 id의 `GET /tasks/{id}`가 404이면 `PASS past_turn_404`.
6. 재무팀: 같은 RAG 질문으로 새 대화를 열어 `completed`이고 `observed_doc_ids`·`cited_doc_ids`에 플랫폼팀 전용 문서(`smoke.py`의 기대값이 쓰는 ID)가 없고 `draft`·`effect`가 없으면 `PASS finance_no_leak_no_send`. **발송 0건 단언은 "작업이 수행되어 completed로 끝남"과 짝으로 판정한다**(아무것도 안 해서 0건인 경우를 통과시키지 않는다, FP-040). 재무팀의 새 대화에서 승인함 `GET /approvals`는 비어 있다.
7. 입력 차단(참고, 결과가 모델·규칙에 의존): 개인정보 형태의 합성 입력을 보내 `blocked_input`이면 `PASS blocked_card_shape`(응답에 `block.code`·`block.message`만 있고 `request`는 `null`이며 보낸 센티널 문자열이 응답 어디에도 없다). 차단되지 않으면 `INFO blocked_card_shape not_blocked`.
8. 모든 `GET /conversations`·`/approvals` 응답을 재귀로 훑어 입력 원문 센티널, `stop_reason`, 판정 근거·출력 경보 키가 없으면 `PASS no_leak_in_views`.
9. 서비스 재시작 뒤 세션이 사라지는 것은 스모크 범위 밖이다(사용자가 수동으로 확인).

### 2. 스텁 서버 계약 테스트 (`tests/test_api_smoke_conversation.py`)

`httpx.ASGITransport`로 연결한 작은 FastAPI 스텁이 위 계약대로 응답하게 하고 `RESULT pass`를 확인한다. 이어서 스텁을 한 군데씩 어긋나게 해서(승인 대기 중 새 턴이 202, 승인 응답에 `effect` 없음, 남의 대화 `GET`이 200, 재무팀 응답에 플랫폼 전용 문서 ID, 3번째 턴이 2번째 턴의 `draft_sha256`을 물려받음, 응답에 센티널 문자열 포함, 재무팀이 `draft` 없이도 `completed`가 아님) 해당 줄이 `FAIL`이고 종료 코드가 1인지 확인한다. 출력에 토큰·답변 원문이 없는지도 확인한다. 마감이 짧을 때 `FAIL`로 끝나고 멈추지 않는다.

### 3. `tests/test_m5_pg.py` (사용자 실행, `GOVOPS_TEST_DATABASE_URL`이 없으면 `pytest.skip`)

- `PostgresTurnIndex`: 무작위 이름의 임시 테이블로 `add`(중복 무시)·`conversation_of`·`set_awaiting`·`awaiting`·`delete_conversations`가 `InMemoryTurnIndex`와 같은 결과이고, 같은 URL의 두 인스턴스가 동시에 처음 쓰기를 해도 DDL 경합이 없다. 끝에서 테이블을 지운다(`test_outbox_pg.py` 방식).
- 보존 삭제: **임시 데이터베이스**(`govops_m5_test_<무작위 hex>`)를 같은 서버에 만들고(URL의 `dbname`만 바꾼 접속, 만들기·지우기는 기본 DB에 연결해 `CREATE DATABASE`/`DROP DATABASE ... WITH (FORCE)`) 그 안에서 `open_checkpointer`로 checkpointer를 준비한다. 작은 그래프로 thread 4개(완료, 오래된 승인 대기, 만료 전 승인 대기, `running`)를 만들고 **`now`를 8일 뒤로 주입**해 `run_prune`을 적용한다. 완료·만료된 대기 thread만 지워지고 나머지는 남으며 `checkpoint.pruned`(`MemoryAuditLog`)가 `{pruned_count, cutoff}`뿐이다. `finally`에서 임시 DB를 지운다. **`GOVOPS_TEST_DATABASE_URL`이 가리키는 DB 자체의 checkpointer 테이블을 만들거나 지우거나 삭제 적용하지 않는다.**
- 모든 DB 대기에 유한한 마감을 둔다(FP-066).

### 4. `Makefile`·`AGENTS.md`

```
.PHONY: smoke-conversation test-m5-pg
# make serve(DEMO_LOGIN=true)·live A·LiteLLM·compose mock 기동 후 사용자 실행, 실제 호출 수 센트
smoke-conversation:
	uv run --env-file .env python -m govops.api.smoke_conversation

# compose Postgres 기동·make db-init 후 사용자 실행, 테스트 전용 DB URL 필요(임시 DB를 만들고 지운다)
test-m5-pg:
	uv run --env-file .env python -c '…'   # test-outbox-pg와 같은 형식, 대상 파일만 tests/test_m5_pg.py
```
`AGENTS.md`의 `## 명령어` 절에 두 줄(`(사용자 실행)` 포함)을 더한다. 둘 다 `make verify`에 넣지 않는다. `tests/test_makefile.py`에 두 타깃이 사용자 실행 전용(`verify` 레시피에 없음, `uv run --env-file .env`)임을 단언하는 테스트를 더한다.

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

- `make smoke-conversation`·`make test-m5-pg`를 실행하지 마라. 이유: 실제 서비스·LLM·Postgres가 필요한 사용자 실행 타깃이다. 검사 논리는 스텁 서버 계약 테스트로만 검증한다.
- `GOVOPS_TEST_DATABASE_URL`이 가리키는 DB의 checkpointer 테이블에서 삭제를 적용하지 마라. 이유: 같은 DB의 실제 대화를 지울 수 있다. 삭제 테스트는 임시 데이터베이스 안에서만 한다.
- 스모크가 "발송 0건"만으로 통과하게 만들지 마라. 이유: 아무것도 하지 않아도 0건이다. 재무팀 사례는 `completed`로 끝났고 권한 밖 문서가 관찰·인용되지 않았음과 함께 판정한다(FP-040).
- 스모크 출력·테스트 로그에 토큰, 요청·답변 원문, 입력 센티널을 남기지 마라. 이유: 토큰과 원문을 화면·로그에 두지 않는다. 세션 토큰은 변수에만 두고 저장하지 않는다.
- 스모크에서 시간 값이나 `sleep` 길이로 순서를 맞추거나 마감 없는 대기를 두지 마라. 이유: 멈추는 스모크는 사용자의 live를 막는다(FP-066).
- 스모크 안에서 `broker`·`mocks`·`evalrun`을 import하지 마라. 이유: 계층 경계다. 효과는 API 응답의 `effect`·mock ID로만 확인한다.
