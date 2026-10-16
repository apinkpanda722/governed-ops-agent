# Step 0: evidence-probe

## 읽어야 할 파일

먼저 아래 파일들을 읽고 프로젝트의 아키텍처와 설계 의도를 파악하라:

- `/AGENTS.md`(계층 경계, CRITICAL 규칙)
- `/docs/ARCHITECTURE.md`, `/docs/ADR.md`(ADR-015·019·026), `/docs/EVAL.md`
- `/docs/M8_DESIGN_DRAFT.md`(이 phase의 승인된 설계), `/docs/results/hypotheses-m8-grader.md`, `/docs/results/hypotheses-m8-citation.md`, `/docs/results/hypotheses-m8-hallucination.md`(사전 등록, 읽기만 한다), `/docs/HUMAN_GRADING_GUIDE_M8.md`
- `/.claude/skills/failure-patterns/SKILL.md` — 읽기만 한다
- `/src/govops/agent/checkpointer.py`(**쓰지 않는다**: `saver.setup()`이 DDL을 시도한다. 연결 방식 참고용으로만 읽는다), `/src/govops/agent/retention.py`, `/src/govops/api/retention.py`(읽기만 한다)
- 설치된 `langgraph/checkpoint/postgres/aio.py`(`AsyncPostgresSaver.from_conn_string`과 `__init__`이 요구하는 연결 인자를 확인한다, 읽기만 한다)
- `/src/govops/agent/graph.py`(`TOOL_RESULT_MAX_CHARS`와 `read_tools_body`의 tool 메시지 구성, 읽기만 한다)
- `/src/govops/api/tasks.py`(`_config`의 `thread_id`, 읽기만 한다)
- `/src/govops/eval/observation.py`(`load_observations`), `/src/govops/obs/langfuse.py`
- `/tests/test_architecture.py`, `/tests/test_startup_cleanup_lock.py`(잠금 계약 가짜 saver 형식), `/tests/test_makefile.py`, `/Makefile`

이전 step에서 만들어진 코드를 꼼꼼히 읽고, 설계 의도를 이해한 뒤 작업하라.

## 작업

**수정할 파일**(이 목록이 전부다): `src/govops/grounding/__init__.py`, `src/govops/grounding/db.py`, `src/govops/grounding/probe.py`, `tests/test_grounding_probe.py`, `tests/test_grounding_db.py`, `tests/test_architecture.py`, `AGENTS.md`, `Makefile`, `tests/test_makefile.py`. TDD(테스트를 먼저 쓰고 통과하는 구현을 쓴다).

열람 금지 경로: `data/interim/`, `data/eval/`, `data/redteam/`
수정 허용 경로: `src/govops/grounding/__init__.py`, `src/govops/grounding/db.py`, `src/govops/grounding/probe.py`, `tests/test_grounding_probe.py`, `tests/test_grounding_db.py`, `tests/test_architecture.py`, `AGENTS.md`, `Makefile`, `tests/test_makefile.py`

열람 금지의 범위: 이 금지는 **Codex 세션이 해당 경로의 파일을 직접 열람·출력·인용하는 것**에 대한 것이다. 이 step의 코드가 런타임에 그 경로를 다루는 것(예: 기본 출력 경로 `data/interim/m8-grounding/`, 평가 명세를 읽는 `load_specs`)은 대상이 아니다. 테스트가 `data/interim/`·`data/eval/`·`data/redteam/`의 실제 파일 **내용**을 읽는 것은 여전히 금지이며 테스트는 임시 디렉터리·합성 명세·합성 관측만 쓴다. 예외로 `data/results/`의 실행 디렉터리는 파일 이름과 JSON 키 이름을 확인하는 데 한해 열람할 수 있고, 응답 본문(`answer`·`effect_bodies`)이나 근거 본문을 출력·인용하지 않는다.

**배경**: 사용자가 2026-10-16에 Postgres 전체 덤프를 만들어 증거 보존 시한은 해소됐다. 이 step은 근거 본문을 채점에 쓸 수 있는지 **개수로만** 점검하는 읽기 전용 도구와 새 패키지의 계층 규칙을 만든다. 근거 본문·ID 값·thread ID는 어떤 출력에도 쓰지 않는다. 이 step에서 실제 DB·Langfuse에는 접속하지 않는다(코드 작성만, 테스트는 가짜 환경).

### 1. 신규 패키지와 계층 규칙

- `src/govops/grounding/__init__.py`: 모듈 docstring 한 줄만.
- `tests/test_architecture.py`(수정 허용: **`LAYERS`에 `"grounding"` 추가, `FORBIDDEN_IMPORTS["grounding"]` 행 추가, 다른 계층이 `govops.grounding`을 import하지 못하는 규칙 추가에 한함. 기존 단언 약화 금지**): `grounding`의 import 금지는 `govops.gateway`, `govops.broker`, `govops.tools`, `govops.api`, `govops.mocks`, `govops.evalrun`, `openai`, `anthropic`, `litellm`이다(`google.genai`·`langgraph`·`psycopg`·`httpx`·`govops.eval`·`govops.obs`·`govops.agent.checkpointer`는 허용). 기존 `evalrun`을 막는 반복문 패턴과 같은 방식으로 다른 모든 계층의 금지 집합에 `govops.grounding`을 더한다. `test_rules_cover_actual_layer_directories`가 새 디렉터리를 포함해 통과해야 한다.
- `AGENTS.md`(수정 허용: **계층 경계 표에 `grounding/` 행 1개 추가, `## 명령어`에 `make m8-evidence-probe` 한 줄 추가에 한함. `CRITICAL:` 문구를 추가·삭제하지 않는다**): 행 문구 — 위치 `grounding/`, 맡는 일 "M8 근거 재구성·채점기 v3·표본·합성 주입(M8 추가 측정, 공식 M6 수치 아님)", import 금지 위 목록. 명령어 줄은 `make m8-evidence-probe # 근거 보존 점검, 개수만 출력(읽기 전용, 사용자 실행)` 형식.

### 1b. `src/govops/grounding/db.py` (DB 읽기를 코드로 읽기 전용으로 만든다)

```python
READONLY_OPTIONS = "-c default_transaction_read_only=on"
def readonly_connect_kwargs() -> dict: ...      # autocommit·prepare_threshold·row_factory는 설치된 aio.py의 from_conn_string과 같게, options=READONLY_OPTIONS
@asynccontextmanager
async def open_readonly_saver(url: str) -> AsyncIterator[AsyncPostgresSaver]: ...
```

- `psycopg.AsyncConnection.connect(url, **readonly_connect_kwargs())`로 연결하고 `AsyncPostgresSaver(conn)`을 직접 만든다. **`setup()`·`asetup()`을 호출하지 않는다**(테이블이 없으면 조회가 실패하고 점검은 FAIL이다, 생성하지 않는다). 종료 시 연결을 닫는다. 연결·종료 오류는 `RuntimeError("readonly_connect_failed")`로 감싸고 원인 체인을 끊는다(`from None`, 메시지에 접속 정보가 있을 수 있다). 연결 문자열·옵션 값은 출력·로그에 남기지 않는다.
- grounding 패키지의 모든 DB 접근(probe·로더·CLI)은 `open_readonly_saver`만 쓴다. `govops.agent.checkpointer.open_checkpointer`를 import하거나 `.setup(`·`.asetup(`를 호출하지 않는다.

### 2. `src/govops/grounding/probe.py`

```python
@dataclass(frozen=True)
class RowLink:
    result_dir: str            # 디렉터리 이름만(경로 아님)
    task_id: str               # 평가 task ID(예: T1-02)
    run_index: int
    trace_id: str | None
    thread_ids: tuple[str, ...]  # 32 hex, 비어 있을 수 있음

def link_results(results_dir: Path) -> list[RowLink]: ...
async def probe_threads(saver, links: Sequence[RowLink]) -> ProbeStats: ...
async def langfuse_counts(client, ...) -> LangfuseStats: ...
def main(argv: list[str] | None = None) -> int: ...
```

- `link_results`: `observations.jsonl`을 `load_observations`로 읽고, `attempts/<task>-<n>/attempt.json`의 `trace_id`가 관측의 `trace_id`와 같은 시도를 찾고, 그 시도의 `audit/chain-*.jsonl` 각 줄을 JSON으로 파싱해 `task_id`가 `^[0-9a-f]{32}$`인 값의 집합에 관측의 `trace_id`(32 hex일 때)를 더한 집합을 `thread_ids`로 한다(실측: T3-05 시도에서 비시스템 감사 이벤트의 `task_id`가 관측 `trace_id`와 같았다). 레코드의 다른 필드는 읽지 않는다. 연결을 못 찾으면 빈 튜플이다(예외로 중단하지 않고 개수로 센다, A-FP-030).
- `probe_threads`: 각 thread ID마다 `saver.aget_tuple({"configurable": {"thread_id": id}})`을 `asyncio.wait_for`(30초)로 부른다. **`saver.alist`를 순회하는 동안 같은 saver를 부르지 않는다**(교착 방지, FP-068). 체크포인트의 `channel_values["messages"]`를 훑어 다음을 센다: 행별 `role=="tool"` 메시지 수, 본문이 비어 있지 않은 수, 본문 길이가 20000 이상이거나 `…(잘림)`으로 끝나는 수, 행별 tool 본문 글자 수 합계(버킷 경계 20000·40000·80000·120000으로 개수만). 메시지가 dict일 수도 LangChain 메시지 객체일 수도 있고 어느 쪽인지 **실측 전이므로** 두 형태를 모두 처리하며 `shape_dict`·`shape_object`·`shape_unknown` 개수를 출력한다.
- Langfuse(`--langfuse` 지정 시에만): 환경 변수 `LANGFUSE_HOST`·`LANGFUSE_PUBLIC_KEY`·`LANGFUSE_SECRET_KEY`(기본값 없는 읽기)를 먼저 확인해 **하나라도 없거나 공백이면 `KeyError`로 죽지 않고 `FAIL langfuse env_unset`을 출력**하며(어느 변수인지 이름은 쓰지 않고 개수 `missing=N`만), 모두 있으면 Langfuse 공개 API의 trace 목록과 observation 목록을 조회해 연결 가능 여부와 **입력(input) 필드가 비어 있지 않은 generation 수**만 센다. 응답 형태가 기대와 다르면 PASS가 아니라 `FAIL langfuse_shape`다. 실제 응답 형태는 이 step에서 확인할 수 없으므로 사용자 첫 실행에서 확인한다(FP-020·050).
- `main` 출력(개수와 코드만, 본문·ID 값 금지): 단계마다 한 줄 `PASS|FAIL|SKIP <stage> key=value …`. stage는 `db_connect`, `link`, `thread_read`, `tool_messages`, `langfuse` 다섯 개 고정이다. `--langfuse`가 없으면 `SKIP langfuse not_requested`로 **사유를 반드시 출력**한다(조용히 건너뛰지 않는다, FP-073). 마지막에 `SUMMARY pass=N fail=N skip=N`. FAIL이 하나라도 있으면 종료 코드 1. 분모 0(`rows=0`)이면 `FAIL link no_rows`(빈 집합 공허 통과 금지, FP-069).
- 인자: `--results DIR [DIR ...]`(필수, 실행 디렉터리), `--url-env NAME`(기본 `GOVOPS_DATABASE_URL`, `os.environ[NAME]`으로 읽고 없으면 `FAIL db_connect url_env_unset`), `--langfuse`. saver는 `db.open_readonly_saver(url)`로 연다(`open_checkpointer` 금지). DB 오류는 예외 **클래스 이름**만 출력한다(메시지에 비밀번호가 있을 수 있다).
- 복원 DB 사용 방법은 `docs/results/hypotheses-m8-grader.md` 2절을 따르며 코드는 URL 환경 변수 이름만 받는다.

### 3. Makefile

`m8-evidence-probe` 타깃: `uv run --env-file .env python -m govops.grounding.probe $(PROBE_ARGS)`. `.PHONY` 선언과 "사용자 실행, 읽기 전용" 주석. `verify` 레시피에 넣지 않는다. `tests/test_makefile.py`(수정 허용: **새 타깃이 `verify`에 없고 `--env-file .env`를 쓰는 사용자 실행 타깃임을 확인하는 테스트 추가에 한함**).

### 4. 테스트 (`tests/test_grounding_probe.py`, 합성 데이터만)

- 임시 결과 디렉터리(합성 `observations.jsonl`·`attempts/…/attempt.json`·`audit/chain-*.jsonl`)로 `link_results`: 정상 연결(`trace_id`와 감사 `task_id`가 같은 경우·다른 경우), trace_id 불일치 시 빈 `thread_ids`, 시스템 이벤트(`task_id` null) 무시, 형식 밖 `task_id` 무시.
- `InMemorySaver`에 합성 체크포인트(dict 메시지 tool 3개 중 1개 `…(잘림)` 끝)로 개수 검증, LangChain 객체 형태 메시지 검증, 알 수 없는 형태 `shape_unknown`.
- 출력 어디에도 합성 본문 표식 문자열·thread ID·task ID 값이 없음을 단언한다(stdout·stderr 전체).
- 잠금 계약 가짜 saver(`tests/test_startup_cleanup_lock.py` 형식, 공유 `asyncio.Lock`)로 `probe_threads`가 `wait_for` 안에 끝남(FP-068).
- DB 연결 실패 → `FAIL db_connect`와 예외 클래스 이름만, 종료 코드 1. `rows=0` → `FAIL link no_rows`. `--langfuse` 없음 → `SKIP langfuse not_requested`. Langfuse는 `httpx.MockTransport`로 정상·형태 불일치(`FAIL langfuse_shape`)·401 경우.
- 다섯 stage가 항상 한 줄씩 출력됨을 단언한다.
- **Langfuse 환경 변수 누락**: 세 변수 중 일부 또는 전부가 없을 때 `--langfuse`가 `FAIL langfuse env_unset missing=N`을 출력하고 예외 없이 종료 코드 1이며, 다른 stage 출력은 유지됨.
- **`tests/test_grounding_db.py`**: `psycopg.AsyncConnection.connect`를 가짜로 바꿔 `options`에 `default_transaction_read_only=on`이 들어 있음(단언만 하고 값을 출력하지 않음), `AsyncPostgresSaver.setup`·`asetup`이 한 번도 호출되지 않음(spy), 연결 실패·종료 실패 시 오류 메시지에 접속 문자열이 없음, 그리고 `src/govops/grounding/*.py`를 AST로 훑어 `open_checkpointer` import와 `.setup(`·`.asetup(` 호출이 없음을 단언(이후 step의 새 파일도 같은 검사를 받는다).

**사용자 실행(이 step에서 실행하지 않는다)**: `make m8-evidence-probe PROBE_ARGS="--results data/results/test/09fbcb2be83f47669800da400a401e55 data/results/test/b330337d70fa4cf9a6c25773fb9b1359 data/results/test/bf38e57a74cc49eda2f2be522f14e063 data/results/dev/6b3ec0b1ec92402db7ebefb2d4cfb1b4 --langfuse"`(live DB), thread가 지워졌으면 임시 DB 복원 뒤 `--url-env GOVOPS_M8_EVIDENCE_DATABASE_URL`을 붙여 같은 점검.

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

- `make m8-evidence-probe`를 실행하지 마라. 실제 Postgres·Langfuse에 접속하는 테스트도 만들지 마라. 이유: 사용자 실행 항목이며 AC는 네트워크 없이 통과해야 한다(AGENTS.md).
- `open_checkpointer`나 `saver.setup()`·`asetup()`을 쓰지 마라. 이유: 스키마 DDL을 시도해 점검이 DB를 바꿀 수 있다. 읽기 전용 연결 옵션으로 연결한 saver만 쓴다.
- probe가 메시지 본문·문서 ID·thread ID·task ID 값·연결 문자열을 출력·로그·예외 메시지에 남기게 하지 마라. 이유: 근거 본문은 보존 예외 대상이고 출력은 개수만 허용한다(hypotheses-m8-grader §2).
- `saver.alist` 순회 안에서 같은 saver를 다시 부르지 마라. 이유: AsyncPostgresSaver가 같은 잠금을 기다려 교착한다(FP-068).
- 분모 0이나 SKIP을 PASS로 출력하지 마라. 이유: 검사 대상이 없는데 통과로 보이면 결함이 숨는다(FP-069·073).
- `tests/test_architecture.py`·`AGENTS.md`는 위에 적은 행·줄만 더하고 `CRITICAL:` 개수와 기존 규칙 행을 바꾸지 마라. 이유: 계층 가드와 문서 일치 테스트가 정확 일치를 본다(FP-061).

## 소비자 점검(시뮬레이션)

`git clone --local` 복사본(`.env`·`data/interim`·`data/raw` 미포함, FP-072)에 빈 `src/govops/grounding/__init__.py`, `AGENTS.md` 표 행·명령어 줄, `Makefile` 타깃 2개, 새 문서·phase 파일을 더하고 전체 pytest를 돌렸다. 처음에는 `tests/test_architecture.py::test_rules_cover_actual_layer_directories`만 실패했다(새 디렉터리에 규칙 행 없음). 이 step의 `tests/test_architecture.py` 수정(`LAYERS`·`FORBIDDEN_IMPORTS` 행)을 더한 뒤 4362개 통과, 실패 4건(`test_faults_client` 3건은 복사본 경로 `/private/tmp`가 `"private" not in` 단언에 걸림, `test_repo_rules` `.venv` 1건은 복사본에 `.venv`가 없음)은 복사본 위치 때문이며 이 변경과 무관하다. `AGENTS.md` 행·명령어 줄 추가는 `tests/test_docs_consistency.py`의 부분 문자열·`CRITICAL:` 개수(26) 단언을 깨지 않았다(`CRITICAL:` 불변). `step_file_checks`의 소비자 경고 10건(`AGENTS.md`·`Makefile` 참조 테스트)은 하나씩 읽고 위 시뮬레이션에서 통과를 확인했다.
