# Step 3: hook-adapter

## 읽어야 할 파일

- `/AGENTS.md`(계층 경계 표의 `gateway/`·`guardrails/` 행, 고정 제약의 Guardrail fail-closed/fail-open), `/docs/ADR.md`(ADR-008·ADR-019 3항), `/docs/THREAT_MODEL.md`(TH9, V11), `/docs/ARCHITECTURE.md`
- 이전 step 산출물: `/src/govops/guardrails/registry.py`·`injection.py`·`screen.py`·`rules_v1.json`(step 2: `rules_sha256`, `screen_request`, `RequestScreen`, `BLOCK_CODES`, `valid_tool_call_id`, `QUARANTINE_MARKER`, `scan_output`)
- `/src/govops/gateway/client.py`(`GatewayError`, 클라이언트 타임아웃 `timeout_s=60.0`), `/src/govops/gateway/agent_key.py`(`AGENT_KEY_MODELS`), `/infra/litellm/config.yaml`·`config.obs.yaml`, `/infra/docker-compose.yml`(`litellm` 서비스), `/tests/test_compose.py`·`/tests/test_makefile.py`·`/tests/test_gateway_client.py`(설정 파일 동일성·모델 목록 테스트가 있다), `/data/seed/users.json`(`users[].team`), `/src/govops/obs/mask.py`
- `/.claude/skills/failure-patterns/SKILL.md`(FP-010·020·021·051) — 읽기만 한다
- **외부 계약 실측**(LiteLLM v1.103.3, 일회용 컨테이너 프로브 + 컨테이너 소스. **성공 여부와 반환 내용을 따로 적는다**. 원자료: `litellm/integrations/custom_guardrail.py`, `litellm/proxy/utils.py`, `litellm/proxy/types_utils/utils.py`):

| 지점 | 성공 여부 | 반환·효과 내용 |
|---|---|---|
| `guardrails:` 설정 로딩 | `guardrail: 모듈.클래스`, `mode: pre_call`/`post_call`, `default_on: true`로 기동 성공 | 모듈은 `config.yaml` 디렉터리 기준 파일을 먼저 찾고 없으면 `PYTHONPATH` import. 컨테이너 Python은 3.13 |
| 사전 훅 `raise HTTPException(400, detail={...})` | HTTP 400 | 본문 `error.provider_specific_fields`에 `detail`의 키가 그대로 실린다(예: `{"error":"guardrail_blocked","code":"injection_direct","guardrail_name":...,"guardrail_mode":"pre_call"}`). `x-litellm-response-cost: 0`, `x-litellm-applied-guardrails` |
| 사전·사후 훅의 예기치 않은 예외 | HTTP 500 | **본문 `error.message`에 예외 메시지 원문이 노출된다.** 훅이 감싸지 않으면 사후 훅 오류도 fail-open이 아니다 |
| 훅 실행 시간 | 훅 자체 타임아웃 없음 | 오래 걸리는 훅은 클라이언트가 끊을 때까지 요청을 붙잡는다 |
| 사전 훅에서 `role:"tool"` 메시지 `content` 수정 | 반영됨 | 모델 호출과 사후 훅의 `data["messages"]`에 수정본이 보인다 |
| 사전 훅의 `data["metadata"]` 쓰기 → `async_post_call_response_headers_hook`가 같은 `data`를 받음 | 반영됨 | 200과 400 응답 모두 헤더 훅이 반환한 헤더가 붙는다. 사후 훅이 쓴 `data["metadata"]` 값도 헤더 훅에서 읽힌다 |
| 사전 훅 400 + `fallbacks`·`content_policy_fallbacks`·`context_window_fallbacks` | fallback 일으키지 않음 | 사후 훅·fallback 모델·`x-litellm-attempted-fallbacks` 흔적 없음. 사전 훅은 요청당 1회 |
| 팀 키 호출 시 훅의 팀 값 | 접근 성공 | `user_api_key_dict.team_id`(인증 객체)와 `data["metadata"]["user_api_key_team_id"]`가 둘 다 `"platform"`이었다. **요청 `metadata`는 호출자가 넣을 수 있으므로 팀의 1차 출처는 인증 객체 `user_api_key_dict.team_id`뿐이다**(metadata의 팀 값은 읽지 않는다). `data["user"]`는 사용자 라벨 |
| 점 경로 import(`guardrail: govops.gateway.guardrail_hook.GovopsGuardrail`) | **성공(P4 실측)** | 빈 `__init__.py` 둘(`govops/`·`govops/gateway/`)과 클래스만 있는 `guardrail_hook.py`를 `src/` 형태로 `:ro` 마운트하고 `PYTHONPATH=/app/src`·`PYTHONDONTWRITEBYTECODE=1`을 주면 기동이 성공하고(`/health/liveliness` 응답) import 오류가 없으며 마운트에 `__pycache__`가 생기지 않았다. `config.yaml` 디렉터리에는 같은 이름의 파일이 없어 `import_module` 경로가 쓰였다 |
| 키의 허용 목록에 없는 모델 요청 | 훅보다 **먼저 인증 단계에서 거절(P7 실측)** | HTTP 403, `error.type == "key_model_access_denied"`, `code: "403"`. 훅은 호출되지 않았다(훅 로그에 허용 모델 호출만 있음). 따라서 허용 모델 확인은 훅 코드(`probe`)가 오는지로 구별한다 |
| 컨테이너 마운트 | **현재 compose는 `config.yaml`만 마운트한다** | 어댑터·`guardrails/`·`obs/mask.py`·`users.json`을 읽기 전용으로 마운트하고 `PYTHONPATH`를 설정해야 한다 |

## 작업

**수정할 파일**(이 목록이 전부다): `src/govops/gateway/hook_digest.py`(신규), `src/govops/gateway/hook_header.py`(신규), `src/govops/gateway/guardrail_hook.py`(신규), `infra/litellm/config.yaml`, `infra/litellm/config.obs.yaml`, `infra/docker-compose.yml`, `tests/test_gateway_hook_digest.py`·`tests/test_gateway_hook_header.py`·`tests/test_gateway_guardrail_hook.py`·`tests/test_gateway_hook_imports.py`(모두 신규), `tests/test_compose.py`·`tests/test_makefile.py`·`tests/test_gateway_client.py`(설정·마운트 단언 추가와 두 yaml 동일성 테스트 갱신에 한함, 기존 단언 약화 금지). 계층: `gateway/`는 `agent`·`tools`·`broker`·`api`를 import할 수 없다. 어댑터는 `litellm`·`fastapi`를 import할 수 있지만 **호스트 `uv run pytest` 환경에는 `litellm`이 설치돼 있지 않다**: 어댑터의 `litellm` import는 모듈 상단에 두되, 테스트는 `sys.modules`에 가짜 `litellm.integrations.custom_guardrail`(최소 `CustomGuardrail` 기반 클래스)을 주입한 뒤 import한다. TDD.

열람 금지 경로: (없음)
수정 허용 경로: `src/govops/gateway/hook_digest.py`, `src/govops/gateway/hook_header.py`, `src/govops/gateway/guardrail_hook.py`, `infra/litellm/config.yaml`, `infra/litellm/config.obs.yaml`, `infra/docker-compose.yml`, `tests/test_gateway_hook_digest.py`, `tests/test_gateway_hook_header.py`, `tests/test_gateway_guardrail_hook.py`, `tests/test_gateway_hook_imports.py`, `tests/test_compose.py`, `tests/test_makefile.py`, `tests/test_gateway_client.py`

**배경**: Guardrail 훅은 LiteLLM 프로세스(컨테이너) 안에서 돈다. 결정 로직은 `guardrails/`(순수 Python, step 2)에 있고, 어댑터는 얇은 껍데기다. 훅은 감사 로그에 쓸 수 없으므로(감사는 호스트 서비스만 쓴다) 판정을 **응답 헤더 `x-govops-guardrail`**로 호스트에 올린다.

### 1. 해시 (`hook_digest.py`, 표준 라이브러리만 + `govops.guardrails.registry`)

- `HOOK_FILES: tuple[str, ...]`: 훅이 import·읽는 파일의 **고정 순서** 목록(루트 기준 상대 경로): `src/govops/__init__.py`, `src/govops/gateway/__init__.py`, `src/govops/gateway/guardrail_hook.py`, `src/govops/gateway/hook_digest.py`, `src/govops/gateway/hook_header.py`, `src/govops/guardrails/__init__.py`, `src/govops/guardrails/injection.py`, `src/govops/guardrails/pii.py`, `src/govops/guardrails/registry.py`, `src/govops/guardrails/screen.py`, `src/govops/guardrails/rules_v1.json`, `src/govops/obs/__init__.py`, `src/govops/obs/mask.py`, `data/seed/users.json`(정렬된 고정 순서. `guardrails/*.py` 중 훅이 import하지 않는 `policy.py`는 제외하되 **import 허용 목록 테스트와 이 목록이 같은 집합**임을 테스트로 고정한다).
- `compute_digests(root: Path) -> HookDigests(rules: str, code: str)`: `rules` = `rules_sha256(root/…/rules_v1.json)`의 앞 12자. `code` = `HOOK_FILES` 순서로 `상대경로 + "\0" + 파일바이트sha256 + "\n"`을 이어 붙인 바이트의 sha256 앞 12자. 파일이 없으면 `FileNotFoundError`(조용한 기본값 금지).
- **컨테이너 쪽 값은 어댑터 모듈을 import할 때 한 번 계산해 모듈 상수로 고정한다**(bind mount라 파일만 바뀌고 프로세스는 옛 모듈을 쓰는 상황을 헤더 값 불일치로 잡기 위해서다. 요청마다 다시 계산하지 않는다). 루트는 어댑터 파일 경로에서 구한다(`Path(__file__).resolve().parents[3]`: 컨테이너에서는 `/app`, 호스트에서는 저장소 루트).

### 2. 헤더 형식 (`hook_header.py`, 표준 라이브러리만)

`x-govops-guardrail: v=1;rules=<12hex>;code=<12hex>;team=<팀|none>;pre=<allow|block|unavailable|team_unknown|probe>;c=<코드|->;q=<n>;m=<n>;ids=<id,id,…|->;bad=<n>;trunc=<0|1>;ms=<n>;post=<ok|flag|error|none>`. 필드 순서와 키는 고정이다.

- `format_header(info: GuardrailInfo) -> str`, `parse_header(value: str | None) -> GuardrailInfo | None`(알 수 없는 키·형식 위반·누락은 `None`. 입력은 신뢰하지 않는다: 길이 상한, 값 문자 집합 검증).
- `q`: 이번 호출에서 격리된 `tool` 메시지 수, `m`: 마스킹된 `tool` 메시지 수. `ids`: 격리된 메시지의 `tool_call_id` 목록(쉼표 구분). **형식 검증** `^(?:call|toolu)_[A-Za-z0-9_-]{1,64}$`(`guardrails.screen.valid_tool_call_id`)에 맞는 것만 넣고(`call_…`와 `toolu_…`가 섞일 수 있다. fallback이 섞인 task), 맞지 않는 격리 메시지는 `bad=<n>`으로 센다. **`ids`는 최대 64개**이고 넘으면 64개만 넣고 `trunc=1`을 붙인다. 이때 `q`는 **실제 격리 수**를 유지한다(호스트는 `trunc=1`이면 건수를 "이상"으로 기록한다).
- `tool_call_id`는 형식 검증 대상이며 PII 정규식을 돌리지 않는다(FP-010).
- `ms`: 훅 안에서 잰 사전 판정 소요(정수 ms). `post`: 사후 훅 결과(`ok`·`flag`(출력 경보)·`error`(훅 오류, 통과 + 경보)·`none`(사후 훅이 돌지 않음, 예: 400)).

### 3. 어댑터 (`guardrail_hook.py`)

`class GovopsGuardrail(CustomGuardrail)`: `litellm.integrations.custom_guardrail.CustomGuardrail`을 상속한다. 설정은 `config.yaml`의 `guardrails:`로 `mode: pre_call`·`post_call` 각각의 항목에서 이 클래스를 로드한다(`default_on: true`).

- 상수: `PRE_CALL_TIMEOUT_S = 2.0`. 이 값은 클라이언트 타임아웃(`gateway/client.py`의 60초)보다 훨씬 짧아야 하며 `0 < PRE_CALL_TIMEOUT_S < 60`과 `== 2.0`을 테스트로 고정한다. 컨테이너의 팀 목록은 시작 시(import 시) `data/seed/users.json`의 `users[].team` 집합에서 읽는다(8개 팀).
- **사전 훅**(`async_pre_call_hook`): 전체를 `asyncio.wait_for(…, PRE_CALL_TIMEOUT_S)`로 감싼다. 순서:
  1. **팀 확인(첫 단계)**: `user_api_key_dict.team_id`(인증 객체)가 없거나 팀 목록 밖이면 **400 `team_unknown`**(전부 차단, 팀 정책으로 폴백하지 않는다). `data["metadata"]`의 팀 값은 읽지 않는다(호출자가 넣을 수 있다).
  2. **진단 프로브**(`data["metadata"]["govops_probe"]`, 팀 확인 **뒤**): `"digest"`이면 **항상** 400 `probe`로 막고 헤더만 붙인다(모델 호출 0회). `"slow"`는 `PRE_CALL_TIMEOUT_S`보다 오래 자고(→ 타임아웃 경로), `"raise"`는 예기치 않은 예외를 낸다(→ 오류 경로). `"post_raise"`는 **아래 3번의 일반 입력 판정을 그대로 거친 뒤** 통과한 경우에만 사후 훅에서 예외를 낸다(→ fail-open 검증용): 사전 판정을 건너뛰지 않는다. 프로브는 **요청을 막거나 느리게 하거나 경보를 만들 뿐이며 어떤 요청도 더 허용하지 않는다**. 테스트로 고정: `post_raise` + 인젝션 입력 → 400 `injection_direct`(사후 훅까지 가지 않음), `post_raise` + PII 입력 → 400 `pii_input`, `post_raise` + 정상 입력 → 통과 후 사후 `post=error`, 알 수 없는 프로브 값은 무시(일반 경로).
  3. `screen_request(data["messages"])`. 차단이면 **400** `HTTPException(400, detail={"error": "guardrail_blocked", "code": <injection_direct|pii_input>})`. 허용이면 `data["messages"]`를 처리된 메시지로 바꾸고 `data["metadata"]`에 판정(격리·마스킹 건수와 `tool_call_id`)을 기록해 `data`를 돌려준다.
  4. **타임아웃·예기치 않은 예외**는 `HTTPException(400, detail={"error": "guardrail_unavailable", "code": "guardrail_unavailable"})`로 바꾼다(fail-closed). **예외 메시지·스택·입력을 응답이나 로그에 남기지 않는다.** 이 코드는 입력 차단(`blocked_input`)이 아니다.
- **사후 훅**(`async_post_call_success_hook`): 전체를 `try/except Exception`으로 감싸 오류는 통과 + 경보(`metadata`에 `post=error`)로 처리한다(fail-open). 정상이면 `scan_output(응답 content)`로 출력 탐지만 하고 `post=flag|ok`를 기록한다. **응답을 바꾸거나 막지 않는다.**
- **헤더 훅**(`async_post_call_response_headers_hook`): `data["metadata"]`에서 판정을 읽어 `x-govops-guardrail` 한 개를 돌려준다. 이 훅도 `try/except`로 감싸 실패하면 `None`을 돌려준다(헤더가 없으면 호스트가 `guardrail_unavailable`로 처리한다). 200·400 모두 붙는다.
- 어댑터는 **어떤 입력·메시지·패턴도 로깅하지 않는다**(`print`·`logging` 금지, 필요하면 규칙 id·건수만).
- `team` 값은 인증 객체의 팀이 팀 목록에 있을 때만 헤더에 넣고 아니면 `none`.

### 4. 컨테이너·설정

- `infra/docker-compose.yml`의 `litellm` 서비스: 읽기 전용 마운트 `../src:/app/src:ro`, `../data/seed/users.json:/app/data/seed/users.json:ro`, 환경 변수 `PYTHONPATH: /app/src`, `PYTHONDONTWRITEBYTECODE: "1"`(읽기 전용 마운트에 `__pycache__`를 쓰지 못한다). **`data/policy/`는 마운트하지 않는다**(훅은 팀 정책을 읽지 않는다). 기존 `config.yaml` 마운트는 그대로다.
- `config.yaml`과 `config.obs.yaml` 모두에 **동일한** `guardrails:` 블록 두 항목(`pre_call`·`post_call`, `guardrail: govops.gateway.guardrail_hook.GovopsGuardrail`)을 넣고 "Guardrail 훅 자리: Guardrail은 M4" 주석을 지운다. 두 파일의 `guardrails` 동일성과 `model_list`·`router_settings` 동일성을 테스트로 고정한다(기존 동일성 테스트를 확장).

### 5. 테스트

- 가짜 `litellm` 주입으로 사전·사후·헤더 훅을 직접 호출한다: 차단(400 코드), 허용(처리된 메시지·헤더), `team_unknown`(팀 없음·팀 밖), 타임아웃(`slow` 프로브, 가상 시간 또는 작은 `PRE_CALL_TIMEOUT_S` 주입 → `guardrail_unavailable`), 예기치 않은 예외(`raise` 프로브) → `guardrail_unavailable`이고 **응답 본문·헤더·캡처한 로그에 예외 문장과 입력이 없음**, 사후 오류(`post_raise`) → 통과 + `post=error`, 헤더 훅 실패 → `None`.
- 해시: 어댑터 import 후 파일(임시 복사본)을 바꿔도 모듈 상수는 그대로이고, 새 프로세스/재import에서만 바뀐다. `compute_digests`는 파일 하나를 바꾸면 `code`가 바뀐다(`rules`는 `rules_v1.json`만 바꿀 때 바뀐다).
- 헤더: 왕복(`format → parse`), `call_`·`toolu_` 혼합 `ids`, 64개 초과 → `trunc=1`·`q`는 실제 수, 형식 밖 id는 `bad`로, 변조·길이 초과·알 수 없는 키는 `None`.
- **빈 `__init__.py` 테스트**: `src/govops/__init__.py`·`src/govops/gateway/__init__.py`·`src/govops/obs/__init__.py`·`src/govops/guardrails/__init__.py`에 `import`·`from … import` 문이 없음을 AST로 단언한다(docstring만 허용). 이 네 파일이 모두 `HOOK_FILES`에 있음을 함께 확인한다. 이유: 컨테이너에서 `govops` 패키지를 점 경로로 import할 때 이 파일들이 실행되므로 다른 계층·무거운 의존성을 끌어오면 안 된다.
- **import 허용 목록 테스트**(`tests/test_gateway_hook_imports.py`): `guardrail_hook.py`·`hook_digest.py`·`hook_header.py`를 AST로 파싱해 `govops.*` import가 `govops.guardrails.*`·`govops.obs.mask`·`govops.gateway.hook_digest`·`govops.gateway.hook_header`뿐이고, 나머지는 표준 라이브러리와 `litellm`·`fastapi`임을 단언한다. 이 허용 집합이 `HOOK_FILES`(`__init__.py`·`json` 제외 규칙은 테스트에 명시)와 모순되지 않음을 함께 확인한다.
- 설정 테스트: 마운트 대상이 존재하는 파일·디렉터리이고 `:ro`이며 `data/policy`를 마운트하지 않고 `PYTHONPATH`·`PYTHONDONTWRITEBYTECODE`가 설정됨.

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

- 훅 안의 예외를 감싸지 않은 채 LiteLLM에 올리지 마라. 이유: 측정상 예기치 않은 예외는 HTTP 500 본문에 예외 문장 원문을 노출하고 사후 훅 오류는 fail-open이 아니다(FP-020). 사전은 `guardrail_unavailable`(fail-closed), 사후는 통과 + 경보다.
- 사전 훅 타임아웃·오류를 입력 차단 코드(`injection_direct`·`pii_input`)로 내보내지 마라. 이유: 호스트가 이를 `blocked_input`(사용자 입력 차단 카드)으로 오인하게 된다. 코드는 `guardrail_unavailable`·`team_unknown`이다.
- 훅에서 입력 메시지·패턴·매치 문자열을 로깅하거나 응답 본문·헤더에 넣지 마라. 이유: 입력 원문과 세부 판정 근거는 응답·로그에 나가면 안 된다. 헤더에는 코드·건수·형식 검증을 통과한 `tool_call_id`·해시 앞 12자만 둔다.
- 팀을 `data["metadata"]`(요청 metadata)에서 읽지 마라. 이유: 호출자가 요청 본문에 `user_api_key_team_id`를 넣을 수 있다. 팀은 인증 객체 `user_api_key_dict.team_id`뿐이다.
- 프로브가 입력 판정을 건너뛰게 하지 마라(`post_raise` 포함). 이유: 진단 경로가 판정 우회 경로가 되면 안 된다. 프로브는 막거나 느리게 하거나 경보만 만든다.
- 어댑터가 `govops.guardrails`·`govops.obs.mask`·`hook_digest`·`hook_header` 밖의 `govops` 모듈(`govops.agent`·`govops.tools`·`govops.audit` 등)을 import하게 하지 마라. 이유: 컨테이너에는 `src/`만 읽기 전용으로 있고 LangGraph·OpenAI SDK 등 의존성이 없다. 허용 목록 테스트로 고정한다.
- 해시를 요청마다 다시 계산하지 마라. 이유: 요청마다 읽으면 파일이 바뀐 것을 프로세스가 옛 코드로 돌고 있는 상황과 구별하지 못한다. 모듈 import 시 한 번 고정해야 `guardrail_hook_stale`이 의미가 있다(FP-051).
