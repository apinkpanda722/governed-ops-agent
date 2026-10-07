# Step 0: verify-speed

## 읽어야 할 파일

- `/AGENTS.md`(개발 프로세스, 명령어), `/Makefile`(`verify` 타깃), `/pyproject.toml`(`[tool.pytest.ini_options]`, dev 의존성), `/tests/conftest.py`
- `/.claude/skills/failure-patterns/SKILL.md`(FP-011·012·023·032) — 읽기만 한다
- 느린 테스트(사전 측정, 직렬 `pytest --durations=40`, 전체 2513 passed·452.78초): `tests/test_eval_taskgen.py`(합 약 130초, 가장 긴 개별 24초: 실제 코퍼스로 명세 생성을 테스트마다 반복), `tests/test_eval_review.py`(약 32초), `tests/test_eval_audience.py`, `tests/test_a_mcp_status.py`(`test_read_retries_and_circuit[connect-*]`·`test_refusal_circuit_blocks_server_connections[*]` 각 7~9초 × 14, 대부분 실시간 sleep), `tests/test_chaos_a.py`(`[delay]` 변형 11~12초 × 3), `tests/test_evalrun_faults.py`(`test_f11_live_assembly_queries_chaos_then_judges_answer[delay]` 12초), `tests/test_evalrun_regrade.py`, `tests/test_evalrun_dev_m3.py`
- 시간·재시도 정책 코드: `/src/govops/tools/resilience.py`(`ToolPolicy`), `/src/govops/tools/a_mcp.py`, `/src/govops/replay/chaos_a.py`

## 작업

**전제**: 사용자가 이 phase 시작 전에 `uv add --dev pytest-xdist filelock`을 실행해 `pyproject.toml`·`uv.lock`을 브랜치에 먼저 커밋했다. 이 step은 의존성을 설치·추가하지 않는다(`pytest-xdist`·`filelock`이 `uv run python -c "import xdist, filelock"`으로 import되지 않으면 즉시 `blocked`).

**수정할 파일**(이 목록이 전부다): `pyproject.toml`(`[tool.pytest.ini_options]`만), `tests/conftest.py`, `tests/corpus_cache.py`(신규: 병렬 안전 공유 생성물 캐시 헬퍼), 느린 테스트 파일 위의 목록 — `tests/test_eval_taskgen.py`, `tests/test_eval_review.py`, `tests/test_eval_audience.py`, `tests/test_a_mcp_status.py`, `tests/test_chaos_a.py`, `tests/test_evalrun_faults.py`, `tests/test_evalrun_regrade.py`, `tests/test_evalrun_dev_m3.py` — 와, **병렬 실행에서 실패하는 것으로 확인된 `tests/` 아래 테스트 파일**과 `scripts/test_*.py`(`testpaths`에 `scripts`가 들어 있어 병렬 실행에 포함된다. 병렬 실패가 확인된 경우에 한해 테스트 파일만 고치고 `scripts/execute.py` 등 비테스트 파일은 고치지 않는다)(파일별로 수정 사유를 최종 보고에 적는다). `Makefile`은 `pytest` 줄이 `-n auto`를 `pyproject.toml`의 `addopts`로 받으므로 고치지 않는다.

열람 금지 경로: (없음)
수정 허용 경로: `pyproject.toml`, `tests/conftest.py`, `tests/corpus_cache.py`, `tests/test_eval_taskgen.py`, `tests/test_eval_review.py`, `tests/test_eval_audience.py`, `tests/test_a_mcp_status.py`, `tests/test_chaos_a.py`, `tests/test_evalrun_faults.py`, `tests/test_evalrun_regrade.py`, `tests/test_evalrun_dev_m3.py`, `tests/`, `Makefile`, `scripts/test_*.py`

**목표**: `make verify`를 로컬에서 3분(180초) 이내로 줄인다. 단언은 약화하지 않는다. TDD: 느린 테스트를 고치기 전에 현재 시간을 `--durations`로 확인하고 고친 뒤 다시 잰다.

### 1. 병렬 실행 (`pyproject.toml`)

- `[tool.pytest.ini_options]`에 `addopts = "-n auto"`를 둔다(`testpaths`는 그대로). 병렬 분배는 기본 `--dist load`를 쓴다. `--dist loadgroup`과 `xdist_group`은 아래 2번의 파일 락 캐시가 불가능한 생성물에 한해 마지막 수단으로만 쓰고, 쓴 곳마다 이유를 주석으로 남긴다.
- 병렬에서 드러나는 순서 의존·공유 자원 테스트(고정 포트, 현재 작업 디렉터리·환경 변수 변경 후 복원 누락, 같은 임시 경로 공유, 모듈 전역 상태)는 **테스트를 고쳐서** 격리한다. 서버를 띄우는 테스트는 `port=0`으로 바인드해 OS가 정한 포트를 읽어 쓴다. 단언 대상을 느슨하게 바꾸지 않는다.

### 2. 공유 생성물의 워커 간 단일 생성 (`tests/corpus_cache.py`, `tests/conftest.py`)

세션 fixture는 xdist 워커마다 따로 돌기 때문에 코퍼스 생성이 워커 수만큼 반복된다. pytest-xdist 문서의 방식을 쓴다: `tmp_path_factory.getbasetemp().parent`(모든 워커가 공유하는 부모 경로) 아래에 `filelock.FileLock`으로 보호한 파일 캐시를 두고, 먼저 락을 얻은 워커가 생성해 JSON 등 직렬화 가능한 형태로 저장하며 나머지는 읽는다.

- 헬퍼 시그니처: `cached_artifact(tmp_path_factory, name: str, build: Callable[[], T], *, dump, load) -> T`(이름 충돌 없이 한 번만 `build`가 호출됨을 단위 테스트로 보인다. 두 프로세스 동시 호출은 `multiprocessing` 대신 두 스레드로 락 경로만 검증해도 된다).
- `tests/test_eval_taskgen.py`·`test_eval_review.py`·`test_eval_audience.py`가 같은 실제 코퍼스 생성 결과를 여러 번 만든다면 이 캐시를 써서 한 번만 만든다. 캐시된 값을 테스트가 **변형하지 않도록** 읽기 전용(깊은 복사 또는 불변 구조)으로 넘긴다.
- 캐시 키에 입력 파일의 sha256을 넣어 데이터가 바뀌면 캐시가 무효가 되게 한다(옛 생성물로 통과하는 것을 막는다).

### 3. 실시간 sleep 정리 (대기 의미가 있는 단언은 유지)

- `tests/test_a_mcp_status.py`·`tests/test_chaos_a.py`·`tests/test_evalrun_faults.py`의 재시도·지연 테스트는 `ToolPolicy`의 **주입 가능한 시계·sleep·base 지연**을 써서 실시간 대기를 줄인다. 주입 지점이 없으면 `ToolPolicy`를 고치지 말고(이 step의 수정 파일이 아니다) 테스트에서 작은 값(`base`, `hard`, `delay`)을 넘긴다.
- **줄이면 안 되는 것**: 2단계 표식 조회의 2.0초 대기(ADR-018)처럼 **대기 자체가 의미인 단언**, `Retry-After` 준수 단언, F10 1.0초·6.0초 경계. 이런 테스트는 가상 시계로 대기 요청 값(예: `sleeps == [2.0]`)을 단언하되 단언을 지우거나 값을 바꾸지 않는다(FP-011).
- FP-023: 서버 도달 수처럼 타이밍에 따라 달라지는 값을 단언하지 않는다. 작은 값으로 줄인 뒤에도 느린 시작 조건에서 통과해야 한다(기존 테스트의 느린 시작 변형은 유지).
- **원복 확인 기록**: 값을 줄인 테스트마다, 줄이기 전 원래 값으로 돌려도 통과하는지를 한 번 실행해 확인하고(확인 뒤 줄인 값으로 되돌린다), 최종 보고(`summary`)에 "테스트 이름: 원래 값 → 줄인 값, 원복 실행 결과"를 적는다. 원복 때 실패하면 줄인 것이 단언 의미를 바꾼 것이므로 줄이지 않는다.

### 4. 측정과 보고

- 고치기 전과 후의 `uv run pytest -q --durations=15` 총 시간과 가장 느린 15개를 최종 보고에 적는다. 목표(3분)를 못 맞추면 `error`가 아니라 `completed`로 두되 못 맞춘 이유와 남은 느린 테스트 상위 10개를 `summary`에 적는다.
- 병렬 실행을 세 번 연속 돌려(`uv run pytest -q`) 모두 통과하는지 확인하고 횟수와 결과를 적는다.

## Acceptance Criteria

```bash
make verify   # ruff check + ruff format --check + pytest(-n auto) + web 검사
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

- 테스트의 단언을 지우거나 느슨하게 바꾸지 마라(범위 비교·`<=`·`xfail`·`skip`·`pytest.approx` 확대 포함). 이유: 속도를 위해 검증 대상이 줄면 verify가 통과해도 안전하지 않다(FP-056). 줄일 수 있는 것은 실시간 대기·중복 생성·직렬 실행뿐이다.
- 입력 고정(`uuid`·시간·난수를 `monkeypatch`로 고정)으로 병렬 실패나 간헐 실패를 덮지 마라. 이유: 무작위·타이밍에서만 나오는 실패는 실제 결함의 신호다(FP-011). 고정이 필요하면 사유 주석과 무작위 대량 테스트를 함께 둔다.
- 서버 도달 수·실제 경과 시간처럼 타이밍에 따라 변하는 값을 새로 단언하지 마라(FP-023). 이유: 병렬 실행은 CPU 경합으로 지연을 늘려 이런 단언을 간헐적으로 깨뜨린다.
- 2단계 조회 2.0초 대기·`Retry-After`·F10 경계 값을 바꾸지 마라(대기 요청 값 단언은 유지). 이유: 이 값들은 ADR-018·ADR-022의 계약이고 테스트가 그것을 증명한다.
- 공유 캐시를 락 없이 쓰지 마라. 이유: 워커가 동시에 같은 파일을 쓰면 반쪽 파일을 읽는 간헐 실패가 생긴다(FP-032).
