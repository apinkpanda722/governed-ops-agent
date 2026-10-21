# Step 4: rollout-wiring

## 읽어야 할 파일

먼저 아래 파일들을 읽고 프로젝트의 아키텍처와 설계 의도를 파악하라:

- `/AGENTS.md`(CRITICAL: 감사 로그 쓰기 실패 시 부작용 금지, 모델 호출은 gateway 클라이언트로만)
- `/docs/M8_DESIGN_DRAFT.md`(2-3절 "서비스 생성"·"조립 위치"·"감사 두 인스턴스"·"기본 off에서 공식 v1 결과 불변 보증")
- `/docs/results/hypotheses-m8-rollout.md`(2·16절, 읽기만 한다)
- `/src/govops/api/wiring.py`(전체 205행: `build_task_service` 46~123행, `service.start` 블록 67~93행, `open_task_service` 126~205행, 조립 블록 173~197행. **읽기만 한다**)
- `/src/govops/api/__main__.py`(전체 44행, lifespan 24~30행)
- `/src/govops/api/retention.py`(step 2가 바꾼 `startup_prune`)
- `/src/govops/api/routed.py`(step 3), `/src/govops/api/variant.py`(step 2), `/src/govops/rollout/store.py`(step 1)
- `/src/govops/audit/log.py`(`_locked` 315~360행 부근의 `_directory_locks`·`fcntl.flock`, `_last_record` 362행~, `append`·`_append_record` 496~527행, 읽기만 한다)
- `/tests/test_api_wiring.py`(`test_missing_configuration` 18~35행, `test_wiring_contract_and_memory` 38~65행, `test_server_lifespan_assembles_and_closes` 104~140행의 `monkeypatch.setattr("govops.api.__main__.open_task_service", …)`)
- `/tests/test_api_entrypoint.py`(전체. 특히 `test_every_service_route_uses_the_service_installed_by_lifespan` 112~133행의 `monkeypatch.setattr(entry, "open_task_service", …)`와 라우트 표 `EXPECTED`)
- `/tests/test_service_start_event.py`(`build` 24~32행, 65~71행의 순차 두 번 조립 → `service.start` 2개)
- `/tests/test_env_defaults.py`(`test_variable_defaults` 표), `/tests/conftest.py`(`serve_asgi`·`a_tokens`)
- `/.claude/skills/failure-patterns/SKILL.md` — 읽기만 한다

## 작업

**수정할 파일**(이 목록이 전부다): `src/govops/api/rollout_wiring.py`(신규), `src/govops/api/__main__.py`(lifespan의 조립 함수 선택과 그 import에 한함), `tests/test_api_rollout_wiring.py`(신규), `tests/test_api_entrypoint.py`(새 테스트 함수 추가에 한함, 기존 테스트·`EXPECTED`·AST 가드 불변), `tests/test_env_defaults.py`(`test_variable_defaults` 표에 행 하나 추가에 한함), `tests/test_api_wiring_semantic.py`(`test_all_service_callers_omit_semantic_except_frozen_fault_runners`의 예외에 `api/rollout_wiring.py`를 정확히 더하는 것에 한함, 아래 3번), `.env.example`(아래 4번의 주석·변수 두 줄씩 추가에 한함). TDD.

열람 금지 경로: `data/interim/`, `data/eval/`, `data/redteam/`, `src/govops/guardrails/rules_v1.json`
수정 허용 경로: `src/govops/api/rollout_wiring.py`, `src/govops/api/__main__.py`, `tests/test_api_rollout_wiring.py`, `tests/test_api_entrypoint.py`, `tests/test_env_defaults.py`, `tests/test_api_wiring_semantic.py`, `.env.example`
열람 금지의 범위: 이 금지는 **Codex 세션이 해당 경로의 파일을 직접 열람·출력·인용하는 것**에 대한 것이다. 이 step의 코드가 런타임에 그 경로를 다루는 것(예: 기본 감사 디렉터리 `data/interim/audit`, 기본 롤아웃 디렉터리 `data/interim/rollout/`)은 대상이 아니다. 테스트가 `data/interim/`·`data/eval/`·`data/redteam/`의 실제 파일 **내용**을 읽는 것은 여전히 금지이며 테스트는 `AUDIT_DIR`·`GOVOPS_ROLLOUT_DIR`를 `tmp_path`로 두고 기존 픽스처(`serve_asgi`·`a_tokens`·가짜 A)만 쓴다. 예외로 `data/results/`의 실행 디렉터리는 파일 이름과 JSON 키 이름을 확인하는 데 한해 열람할 수 있고, 응답 본문(`answer`·`effect_bodies`)이나 근거 본문을 출력·인용하지 않는다. 이 제한은 이 step이 새로 작성하는 테스트에 한한 것이며, 기존 `tests/test_m7_freeze.py`가 `scripts/freeze_digest.py`로 해시를 계산하며 동결 경로 파일을 바이트로 읽는 것은 `make verify`의 일부이므로 허용한다. Codex 세션이 그 파일 내용을 출력·인용하는 것은 여전히 금지다.

`src/govops/api/` 아래 새 파일에는 `redteam` 문자열을 쓰지 않는다(`tests/test_redteam_boundary.py`가 `api/`의 모든 `.py`를 검사한다).

**목적**: 롤아웃이 켜졌을 때만 v1·v2 서비스 두 개와 라우터를 조립하고, 꺼져 있으면(기본) 서버가 지금과 똑같이 `open_task_service` 하나를 쓰게 한다.

### 1. `src/govops/api/rollout_wiring.py`

```python
def rollout_enabled() -> bool: ...        # env_or("GOVOPS_V2_ROLLOUT", "off"): "on" → True, "off" → False, 그 밖 → StartupError("GOVOPS_V2_ROLLOUT")

@asynccontextmanager
async def open_rollout_service(): ...     # yield RoutedTaskService
```

`open_rollout_service` 순서:
1. `env_or("GOVOPS_GUARDRAIL_V2", "off") == "on"`이면 **어떤 감사 쓰기·외부 접속보다 먼저** `StartupError("rollout_conflict")`.
2. `open_task_service`(`wiring.py:126-197`)의 사전 조립을 **같은 순서·같은 오류 코드**로 다시 쓴다: fault mode 거부, 비밀 값 등록, trace sink 설정, `TeamKeyResolver`와 `team_keys_missing`, `KODOCS_MCP_URL` 필수, `AMcpClient`·`verify_contract`, 공유 `httpx.AsyncClient(trust_env=False)`, `ToolPolicy`·`ToolRuntime`, `open_checkpointer`, `GOVOPS_AGENT_PROFILE` 검사, outbox, 공유 턴 색인. `wiring.py`의 함수는 import해서 그대로 쓰되(`build_task_service`, `_FROM_ENV`는 쓰지 않음) **`wiring.py`는 수정하지 않는다**.
3. 변형 저장소: `GOVOPS_DATABASE_URL`이 있으면 `PostgresVariantStore()`, 없으면 `InMemoryVariantStore()`.
4. chat 하나(`LiteLLMChatClient`, `open_task_service`와 같은 인자). `classify`가 호출 가능하지 않으면 `StartupError("guardrail_v2_unavailable")`(서비스 조립 전).
5. `directory = store.rollout_dir()`. v1 = `build_task_service(chat=chat, …, turn_index=VariantFilteredTurnIndex(turn_index, variants, "v1"), semantic=None)`, v2 = `build_task_service(chat=chat, …, turn_index=VariantFilteredTurnIndex(turn_index, variants, "v2"), semantic=RecordingClassifier(chat, directory=directory))`. 두 서비스는 saver·outbox·도구·chat·감사 디렉터리(`AUDIT_DIR`)를 공유한다. `semantic`은 반드시 명시적으로 넘긴다(환경 변수 경로 `_FROM_ENV`를 타지 않게).
6. `router = RoutedTaskService(v1, v2, turns=turn_index, variants=variants, directory=directory)`. `await router.startup_cleanup()`(v1에서 한 번), `await startup_prune(router, saver, turn_index, variants=variants)`(한 번).
7. `yield router`. 종료 때 `router.aclose()`, 그 뒤 REST 클라이언트 닫기(`open_task_service`와 같은 `finally` 구조).

### 2. `src/govops/api/__main__.py`

lifespan의 `async with open_task_service() as service:`를 아래처럼 바꾸는 것에 한한다(나머지 줄 불변):

```python
opener = open_rollout_service if rollout_enabled() else open_task_service
async with opener() as service:
```

두 이름은 lifespan 실행 시점에 모듈 전역에서 찾으므로 기존 테스트의 `monkeypatch.setattr(entry, "open_task_service", …)`·`monkeypatch.setattr("govops.api.__main__.open_task_service", …)`가 그대로 통한다. `app.state.audit = getattr(service, "audit", None)`은 그대로 두며 라우터의 `audit` 속성이 쓰인다.

### 3. 테스트

`tests/test_api_rollout_wiring.py`:
1. **동등성(어긋남 방지)**: `test_missing_configuration`과 같은 입력(`KODOCS_MCP_URL` 누락, 팀 키 누락)과 `GOVOPS_MODEL_FAULT_MODE` 설정에서 `open_rollout_service`가 `open_task_service`와 **같은** `StartupError` 문자열을 내고 `__context__`가 `None`이다(parametrize로 두 opener를 함께 돌림).
2. 충돌: `GOVOPS_GUARDRAIL_V2=on` + 롤아웃 켬 → `rollout_conflict`, 감사 디렉터리(`tmp_path`)에 `chain-*.jsonl`이 없음, 가짜 A에 요청이 가지 않음.
3. `rollout_enabled`: 미설정·빈 값·`off` → False(FP-030), `on` → True, 그 밖 → `StartupError("GOVOPS_V2_ROLLOUT")`.
4. 조립(가짜 A, `test_wiring_contract_and_memory`와 같은 픽스처, `GOVOPS_DATABASE_URL=""`, `AUDIT_DIR`·`GOVOPS_ROLLOUT_DIR`=`tmp_path` 아래): `RoutedTaskService`가 나오고, 두 서비스의 `graph.checkpointer`가 같은 객체, v1 `graph.agent_semantic is None`, v2 `graph.agent_semantic`이 `RecordingClassifier`, 감사 체인의 `service.start`가 정확히 2개이고 `verify_chain` 통과, `startup_cleanup`·`startup_prune`이 각각 한 번(`monkeypatch`로 감싼 호출 기록).
5. **롤아웃 off 경로 불변**: 환경 변수 미설정으로 `build_server_config`의 lifespan을 돌리면 `open_task_service`(가짜로 바꿈)가 한 번 불리고 `open_rollout_service`(호출되면 실패하는 가짜)는 불리지 않는다. 실제 `open_task_service`를 가짜 A로 돌린 경우 `service.start`가 정확히 1개다.
6. **감사 두 인스턴스(지시 46 7번)**: `tests/test_service_start_event.py`의 `build`와 같은 방식으로 같은 `tmp_path`에 `build_task_service`를 두 번 불러 두 `audit`(`AuditLog`)을 얻는다. (a) 두 인스턴스가 번갈아 `append("conversation.deleted", task_id=<hex32>, actor_sub="demo-platform-g3")`를 10번씩 쓰고, (b) 두 스레드가 각각 20번씩 동시에 쓴다. 각 경우 체인의 seq가 1씩 늘고 중복·빈 번호가 없으며 `verify_chain(tmp_path).ok`. 단언은 seq 집합·개수로 한다(FP-012).

`tests/test_api_entrypoint.py`(새 함수 추가): `GOVOPS_V2_ROLLOUT=on`에서 `monkeypatch.setattr(entry, "open_rollout_service", fake)`로 바꾸면 라우트 표 `EXPECTED`의 모든 라우트가 그 가짜를 부르고 `app.state.audit`이 가짜의 `audit`이다(기존 `FakeService` 재사용). 기존 테스트 함수·`EXPECTED`·`violations`는 바꾸지 않는다.
`tests/test_env_defaults.py`: `("api/rollout_wiring.py", "GOVOPS_V2_ROLLOUT", "off")` 한 행.
`tests/test_api_wiring_semantic.py::test_all_service_callers_omit_semantic_except_frozen_fault_runners`(84~110행): 이 테스트는 `src/govops/` 전체의 `build_task_service` 호출을 모아 `semantic` 키워드를 금지하고(예외 `evalrun/faults.py`·`evalrun/faults_smoke.py`는 상수 `None`) 호출자 정렬 목록을 정확히 단언한다. `api/rollout_wiring.py`의 두 호출은 반드시 `semantic`을 넘기므로 이 테스트를 **정확한 예외로만** 넓힌다: `api/rollout_wiring.py`에서 `semantic` 키워드가 정확히 하나이고 그 값이 상수 `None`이거나 이름이 `RecordingClassifier`인 호출(`ast.Call`)이며, 파일 안에서 `None` 호출 하나·`RecordingClassifier` 호출 하나가 정확히 있다. 기대 목록에 `"api/rollout_wiring.py"` 두 개를 더한다. 그 밖의 파일에 대한 단언(`keywords == []`, 기존 목록 항목)은 바꾸지 않는다(FP-060: 예외 집합을 정확히 고정).

### 4. `.env.example`

AGENTS.md CRITICAL("필요한 변수 이름은 `.env.example`에 적는다")에 따라 `GOVOPS_GUARDRAIL_V2=` 줄 바로 뒤에 아래 네 줄을 더한다(값은 비워 둔다. `tests/test_repo_rules.py::test_env_example_has_only_empty_assignments`가 `NAME=` 형식만 허용한다):

```
# M8 v2 점진 배포 스위치: on|off, 미설정·빈 값은 off. GOVOPS_GUARDRAIL_V2=on과 함께 켜면 시작 거부(M8 추가 측정)
GOVOPS_V2_ROLLOUT=
# M8 롤아웃 상태·노출·분류 기록 디렉터리, 미설정·빈 값은 data/interim/rollout
GOVOPS_ROLLOUT_DIR=
```

`.env`는 열거나 고치지 않는다.

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

- `src/govops/api/wiring.py`와 `tests/test_api_wiring.py`·`tests/test_api_wiring_build.py`·`tests/test_service_start_event.py`를 수정하지 마라. 이 테스트들은 수정 없이 통과해야 한다. `tests/test_api_wiring_semantic.py`는 3번의 예외 추가 외에는 바꾸지 않는다. 이유: 롤아웃 off 경로가 지금과 같다는 증거이고 기존 함수 본문 불변이 원칙이다(설계 1-2·2-3절).
- `build_task_service`를 `semantic` 인자 없이 부르지 마라. 이유: 생략하면 `GOVOPS_GUARDRAIL_V2` 환경 변수 경로(`_FROM_ENV`)를 타서 v1 서비스에 v2가 켜질 수 있다.
- `rollout_conflict` 검사를 감사 쓰기·A 접속 뒤에 두지 마라. 이유: 거부된 시작이 `service.start`를 남기면 감사 개수 가정이 깨진다(설계 6절 9행).
- `__main__.py`에서 `open_task_service`를 다른 이름으로 import하거나 lifespan 밖에서 미리 고르지 마라. 이유: 기존 진입점 테스트의 `monkeypatch.setattr(entry, "open_task_service", …)`가 통하지 않게 된다(FP-070).
- 실제 LiteLLM·A MCP·Postgres에 접속하는 테스트를 만들지 마라. 이유: AC는 네트워크 없이 통과해야 한다. 가짜 A(`govops.replay.fake_a`)와 `serve_asgi`만 쓴다.
