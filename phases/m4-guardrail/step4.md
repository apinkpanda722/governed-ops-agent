# Step 4: team-keys

## 읽어야 할 파일

- `/AGENTS.md`(계층 경계 표의 `gateway/`·`api/` 행, 고정 제약의 토큰·비용 규칙), `/docs/ADR.md`(ADR-007·ADR-008·ADR-019 4·10항), `/docs/ARCHITECTURE.md`(게이트웨이 절)
- 이전 step 산출물: `/src/govops/gateway/hook_header.py`(`GuardrailInfo`, `parse_header`), `/src/govops/gateway/hook_digest.py`(`compute_digests`, `HookDigests`), `/src/govops/gateway/guardrail_hook.py`(응답 코드 `guardrail_blocked`+`code`: `injection_direct`·`pii_input`, `guardrail_unavailable`, `team_unknown`)
- `/src/govops/gateway/client.py`(`LiteLLMChatClient.complete`·`_send`·`GatewayError`·`ChatResult`·정산 `finally`), `/src/govops/gateway/agent_key.py`(`AGENT_KEY_MODELS`, `_write_atomic`, `_ASSIGNMENT`, `_fail`, `main`, `--update`), `/src/govops/gateway/smoke.py`, `/src/govops/api/wiring.py`(`open_task_service`), `/src/govops/api/smoke.py`, `/src/govops/agent/users.py`(`load_users`, `UserInfo.team`), `/data/seed/users.json`, `/Makefile`(`agent-key`·`agent-key-update`), `/.env.example`
- `/tests/test_agent_key.py`, `/tests/test_gateway_client.py`, `/tests/test_smoke_gateway.py`, `/tests/test_api_wiring.py`, `/tests/test_api_wiring_build.py`, `/tests/test_api_smoke.py`, `/tests/test_e2e_isolation.py`
- `/.claude/skills/failure-patterns/SKILL.md`(FP-010·020·021·030) — 읽기만 한다
- **외부 계약 실측**(LiteLLM v1.103.3 일회용 프로브, **성공 여부와 반환 내용을 따로 적는다**):

| 지점 | 성공 여부 | 반환·효과 내용 |
|---|---|---|
| `POST /team/new` `{"team_id": "platform", "team_alias": "platform", "models": [...], "max_budget": 1000}` | 성공 | `team_id`·`team_alias`·`models`·`max_budget`을 그대로 돌려준다. `data-ai`처럼 하이픈이 든 id도 된다 |
| 같은 `team_id`로 다시 `POST /team/new` | **실패(오류)** | 본문 `"Team id = platform already exists. Please use a different team id."`(`code:"400"`). **멱등하게 만들려면 먼저 존재를 확인해야 한다** |
| `POST /key/generate` `{"team_id","models","key_alias"}` | 성공 | `key`(문자열)를 돌려준다. 이 키로 호출하면 훅에 `user_api_key_team_id`가 팀 id로 전달된다 |
| 400 응답의 `x-govops-guardrail` 헤더 | 붙는다 | 사전 훅이 막은 400에도 헤더 훅이 돈다 |
| `GET /team/info?team_id=<없는 팀>` (P5 실측) | 404 | `{"error":{"message":"{'message': 'Team not found, passed team id: <id>.'}","type":"auth_error","param":"None","code":"404"}}` |
| `GET /team/info?team_id=<있는 팀>` (P5 실측) | 200 | 최상위 키 `keys`·`team_id`·`team_info`·`team_memberships`. 존재 확인은 **상태 코드 200/404만**으로 한다(본문 문구에 의존하지 않는다) |
| 같은 `key_alias`로 `POST /key/generate` 두 번째 (P6 실측) | **실패 HTTP 400** | 첫 호출 200(`key` 반환), 둘째는 `{"message": "Key with alias 'team-platform' already exists. Unique key aliases across all keys are required.", "type": "bad_request_error", "param": "key_alias", "code": "400"}`. 키 값은 다시 돌려주지 않는다(`/key/list`는 해시·메타데이터만) — **`.env`의 값을 잃으면 복구할 수 없다** |
| `POST /key/delete` `{"key_aliases": ["team-platform"]}` (실측) | 200 | `{"deleted_keys": ["team-platform"]}`. 삭제 뒤 같은 alias로 `/key/generate`가 다시 200 |

## 작업

**수정할 파일**(이 목록이 전부다): `src/govops/gateway/team_keys.py`(신규), `src/govops/gateway/client.py`, `src/govops/gateway/smoke.py`, `src/govops/api/wiring.py`, `src/govops/api/smoke.py`, `Makefile`(`team-keys`·`team-keys-update` 타깃 추가), `.env.example`(팀 키 변수 이름 추가·`LITELLM_AGENT_KEY` 주석 갱신), `tests/test_team_keys.py`(신규), `tests/test_gateway_client.py`·`tests/test_smoke_gateway.py`·`tests/test_api_wiring.py`·`tests/test_api_wiring_build.py`·`tests/test_api_smoke.py`·`tests/test_e2e_isolation.py`·`tests/test_agent_key.py`·`tests/test_makefile.py`(위 변경으로 깨지는 기대값 갱신과 신규 케이스 추가에 한함, 단언 약화 금지). **`evalrun/`은 이 step에서 고치지 않는다**(다음 phase step). `gateway/agent_key.py`는 읽기만 한다(`AGENT_KEY_MODELS`는 그대로 import해서 쓴다). 계층: `gateway/`는 `agent`·`tools`·`broker`·`api`를 import할 수 없다 — 팀 정보는 `gateway/`가 `agent.users`를 import하지 않고 `data/seed/users.json`을 직접 읽거나 호출자가 `Mapping[str, str]`(사용자 라벨 → 팀)로 넘긴다. `api/wiring.py`는 `agent`·`gateway`를 import할 수 있다. TDD.

열람 금지 경로: (없음)
수정 허용 경로: `src/govops/gateway/team_keys.py`, `src/govops/gateway/client.py`, `src/govops/gateway/smoke.py`, `src/govops/api/wiring.py`, `src/govops/api/smoke.py`, `Makefile`, `.env.example`, `tests/test_team_keys.py`, `tests/test_gateway_client.py`, `tests/test_smoke_gateway.py`, `tests/test_api_wiring.py`, `tests/test_api_wiring_build.py`, `tests/test_api_smoke.py`, `tests/test_e2e_isolation.py`, `tests/test_agent_key.py`, `tests/test_makefile.py`

### 1. 팀·팀 키 발급 (`gateway/team_keys.py`)

- `TEAM_MAX_BUDGET_USD = 1000.0`: **구속하지 않는 값**이다. 실제 비용 상한은 B 총액 원장(ADR-007)이 맡고 팀 예산은 사용량 귀속용이다. 상수 옆 주석에 이 의도를 쓴다.
- `load_teams(path="data/seed/users.json") -> tuple[str, ...]`(정렬된 중복 없는 팀 집합, 8개), `team_env_name(team: str) -> str`(`"LITELLM_TEAM_KEY_" + team.upper().replace("-", "_")`: `LITELLM_TEAM_KEY_DATA_AI` 등).
- `class TeamKeyResolver(users: Mapping[str, str], env: Mapping[str, str] = os.environ)`: `key_for(user_id: str) -> str`(사용자 → 팀 → 환경 변수. 빈 문자열·공백은 미설정(`env_or`와 같은 의미, FP-030), 사용자·팀·키가 없으면 `GatewayError("team_key_missing")`로 **조용한 대체 없이** 실패), `missing() -> list[str]`(키가 비어 있는 팀 이름). 키 값은 어디에도 출력·로깅하지 않는다.
- CLI `python -m govops.gateway.team_keys [--env-file .env] [--update] [--rotate] [--base-url …]`(`make team-keys`·`make team-keys-update`, 사용자 실행. Makefile은 `team-keys: uv run --env-file .env python -m govops.gateway.team_keys $(TEAM_KEYS_ARGS)`로 두어 `make team-keys TEAM_KEYS_ARGS=--rotate`로 회전한다. 별도 타깃을 더하지 않는다): 팀마다 ① **`GET /team/info`가 404일 때만** `POST /team/new`(`team_id`=`team_alias`=팀 이름, `models=list(AGENT_KEY_MODELS)`, `max_budget=TEAM_MAX_BUDGET_USD`; 200이면 건너뜀, 그 밖의 상태는 `FAIL team_info_http`), ② 해당 환경 변수에 값이 **있으면 키 호출을 하지 않는다**(멱등: 재실행해도 변화·오류 없음). 비어 있으면 `POST /key/generate`(`team_id`, `models=list(AGENT_KEY_MODELS)`, `key_alias=f"team-{team}"`)로 키를 만들어 `.env`에 `agent_key.py`와 같은 방식(원자적 쓰기·0600 유지·기존 줄 갱신)으로 기록한다. **생성이 400 + `param == "key_alias"`(alias 중복)이면 키가 서버에 이미 있는데 값을 복구할 수 없는 상태이므로 `FAIL key_exists_unrecoverable:<팀>`으로 끝내고 `.env`를 바꾸지 않는다**(실측 P6). `--rotate`를 주면 **환경 변수가 비어 있는 팀에 한해** `POST /key/delete`(`{"key_aliases": [alias]}`) 뒤 다시 생성해 기록한다(이미 값이 있는 팀의 키는 절대 삭제하지 않는다). 이미 값이 있는 변수는 건드리지 않는다. `--update`는 기존 팀·키의 `models`를 `AGENT_KEY_MODELS`로 갱신한다(`/team/update`·`/key/update`; `.env` 값은 바뀌지 않는다). **`AGENT_KEY_MODELS`는 `agent_key.py`가 유일한 정의**다(복사 금지, 같은 객체임을 테스트로 고정). 출력에는 `PASS`/`FAIL`과 팀 이름·단계 코드만 쓰고 키 값·마스터 키·응답 본문을 쓰지 않는다. 마스터 키가 없으면 `FAIL missing_master_key`.

### 2. 클라이언트 (`gateway/client.py`)

- `LiteLLMChatClient.__init__`에 키워드 인자 추가: `key_resolver: Callable[[str], str] | None = None`, `expected_digests: HookDigests | None = None`, `require_guardrail: bool = False`. 기존 `api_key`·`LITELLM_AGENT_KEY` 경로는 `key_resolver`가 없을 때만 쓴다(서비스 경로는 아래 3번에서 `key_resolver`를 필수로 쓴다). `complete`는 호출마다 `key_resolver(user_id)`로 키를 정하고 그 키를 SDK 호출에만 쓴다(state·메타데이터·로그·예외 문구에 넣지 않는다).
- 새 예외(둘 다 `GatewayError`의 하위 클래스, 안전한 코드 문자열만 가진다): `GuardrailBlocked(code, info)`(`code ∈ {"injection_direct","pii_input"}`)와 `GuardrailUnavailable(code)`(`code ∈ {"guardrail_unavailable","team_unknown","guardrail_header_missing","guardrail_stale"}`). 기존 `except GatewayError` 처리는 그대로 동작한다.
- 400 응답 해석(`APIStatusError`): 본문 `error.provider_specific_fields.code`를 읽는다. `injection_direct`·`pii_input` → `GuardrailBlocked`, `guardrail_unavailable`·`team_unknown` → `GuardrailUnavailable`, 그 밖의 400은 기존 `GatewayError("gateway HTTP 400")`. **응답 본문·헤더 원문을 예외 메시지에 넣지 않는다.** 응답 헤더 `x-govops-guardrail`은 `parse_header`로 읽어 `GuardrailBlocked.info`에 담는다.
- 성공 응답: 헤더를 파싱해 `ChatResult.guardrail: GuardrailInfo | None = None` 필드(기본값 `None`, 기존 호출자 호환)에 담는다. `require_guardrail=True`일 때 헤더가 없거나 형식이 틀리면 `GuardrailUnavailable("guardrail_header_missing")`을 던지고, `expected_digests`가 있고 헤더의 `rules`·`code`가 다르면 `GuardrailUnavailable("guardrail_stale")`을 던진다(훅이 로드되지 않았거나 낡은 채로 **조용히 Guardrail 없이** 실행되는 것을 막는다, FP-051). 두 오류 모두 모델 호출 응답이 이미 왔어도 그 결과를 쓰지 않는다. 비용은 호출이 일어났으므로 정산한다(기존 `finally`).
- **차단된 호출의 정산**: `GuardrailBlocked`·`GuardrailUnavailable`(공급사 호출 없음)은 예약을 0원으로 정산한다(기존 `finally`에서 `supplier`가 정해지지 않으면 0원이다). 이 동작과 `agent_primary`·`agent_fallback` 예약이 모두 0원으로 닫히는 것을 테스트로 고정한다. 400 `GuardrailBlocked` 뒤 원장에 미정산 예약이 남지 않는다.

### 3. 서비스 경로 (`api/wiring.py`, `api/smoke.py`, `gateway/smoke.py`)

- `open_task_service`: `LITELLM_AGENT_KEY` 필수 검사를 **팀 키 검사로 바꾼다**. 사용자 시드의 모든 팀에 키가 있어야 하고 하나라도 비어 있으면 `StartupError("team_keys_missing")`로 시작하지 않는다. 클라이언트는 `LiteLLMChatClient(key_resolver=resolver.key_for, expected_digests=compute_digests(<저장소 루트>), require_guardrail=True)`로 만든다. **기존 에이전트 키로 조용히 폴백하지 않는다.**
- `gateway/smoke.py`(`smoke-fallback`)·`api/smoke.py`(`smoke-agent`): 필요한 환경 변수 목록에서 `LITELLM_AGENT_KEY`를 `SMOKE_USER`의 팀 키로 바꾸고, 클라이언트는 같은 방식(팀 키·`require_guardrail=True`·`expected_digests`)으로 만든다. 환경 변수 누락 안내는 변수 이름만 쓴다.
- `.env.example`: 팀 키 변수 이름 8개(`LITELLM_TEAM_KEY_DATA_AI` 등)를 빈 값으로 추가하고 `LITELLM_AGENT_KEY` 주석을 "진단 전용(Guardrail은 팀 없는 키를 `team_unknown`으로 막는다). 서비스 경로는 쓰지 않는다"로 바꾼다.

### 4. 테스트

- `TeamKeyResolver`: 사용자→팀→키 선택(두 팀이 서로 다른 키), 빈 값·공백·누락·모르는 사용자 → `team_key_missing`, 키 값이 예외 문구·`repr`에 없음.
- CLI(`httpx.MockTransport`, **실측한 응답 형태를 픽스처로**): `team/info` 200(생성 호출 없음)·404(생성), 그 밖의 상태 → `FAIL team_info_http`, 키가 이미 `.env`에 있으면 `key/generate` 호출 자체가 없음, 재실행 멱등, alias 중복 400 → `FAIL key_exists_unrecoverable`(`.env` 불변, 키 값 출력 없음), `--rotate`: 환경 변수가 비어 있는 팀만 delete → generate, 값이 있는 팀은 delete 호출 없음, `.env`에 키 값이 기록되고 출력·실패 메시지에 키가 없음, `AGENT_KEY_MODELS`가 팀·키 요청에 그대로 쓰임(`agent_key.AGENT_KEY_MODELS is`로 같은 객체).
- 클라이언트(가짜 `httpx` 전송, **실측한 400 본문 형태**를 픽스처로 쓴다: `error.provider_specific_fields.{error,code,guardrail_name,guardrail_mode}`): 4가지 코드 매핑, 400의 헤더 `info`, 성공 응답의 `ChatResult.guardrail`, 헤더 없음/형식 오류 + `require_guardrail` → `guardrail_header_missing`, 다이제스트 불일치 → `guardrail_stale`(**로직 파일만 바뀌고 재기동하지 않은 경우를 가짜 헤더(`code` 12자가 다름)로 고정**), 차단 후 원장 미정산 예약 0·0원 정산, 키가 예외·로그에 없음, 호출마다 사용자의 팀 키가 쓰임.
- 서비스 조립: 팀 키가 하나라도 없으면 `team_keys_missing`, 에이전트 키만 있으면 시작 안 함.

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

- 존재 확인(`GET /team/info`, 200/404) 없이 `/team/new`를, 환경 변수에 값이 있는데 `/key/generate`를 호출하지 마라. 이유: 실측대로 같은 `team_id`·같은 `key_alias` 재생성은 오류이고 기존 키 값은 복구할 수 없어 `make team-keys` 재실행이 실패하거나 키를 잃는다(FP-050). `--rotate`도 값이 있는 팀의 키는 지우지 않는다.
- 환경 변수를 `os.environ.get(name, 기본값)`으로 읽거나 빈 값을 키로 쓰지 마라. 이유: `.env`의 빈 값이 기본값을 덮는다(FP-030). 키 누락은 `team_key_missing`·`team_keys_missing`으로 드러나야 한다.
- 팀 키가 없을 때 `LITELLM_AGENT_KEY`로 폴백하지 마라. 이유: 에이전트 키는 팀이 없어 훅이 `team_unknown`으로 막고, 조용한 폴백은 팀별 귀속을 무너뜨린다.
- 가짜 응답을 추측한 형태로 만들지 마라. 이유: 400 본문은 실측한 `provider_specific_fields` 형태를 픽스처로 써야 한다(FP-020). 헤더 검증은 `code`·`rules` 두 값을 독립으로 단언한다(FP-021).
- 키·마스터 키·응답 본문·헤더 원문을 예외 문구·로그·출력·테스트 스냅샷에 남기지 마라. 이유: 토큰 비노출은 CRITICAL이다.
