# M8 설계 초안: 감사 체인에 도구 호출 기록 (m8-tool-audit)

**M8 추가 — 공식 M6 수치 아님.** 기준: main `6d40ee9`. 지시 47(사용자 결정 2026-10-21, 동결 경계 변경 승인). 이 문서는 설계 초안이며 코드는 만들지 않았다. 공식 M6 수치는 변경 전 코드로 낸 값이고 바꾸지 않는다.

표기: **실측**은 파일:행 또는 실행으로 확인한 사실이다. 열람하지 않은 경로: `.env`, `data/interim/`의 내용, `data/eval/`·`data/redteam/` 원문.

## 0. 배경

지금 감사 체인은 쓰기 도구만 남긴다(`side_effect.draft`의 `draft_sha256`, `side_effect.attempt`·`result`의 `args_sha256`). 읽기 도구 호출은 trace span(`tool.result.ids`)에만 있다. ADR-012가 감사 범위를 "책임 추적과 보안·부작용 사건"으로 정했을 뿐 읽기를 뺀다는 결정은 따로 없었다. 이 작업은 모델이 낸 모든 tool_call을 위변조 탐지 가능한 감사 체인에 남긴다.

## 1. 사용자 결정 (2026-10-21)

| # | 결정 |
|---|---|
| 1 | 동결 경계 변경 승인(지시 47) |
| 2 | 읽기 호출 인자는 **HMAC-SHA256**으로 기록한다. 감사 서명 키(Ed25519)와 분리된 전용 키를 쓰고(재사용 금지), 키는 `.env`에만 두며 생성은 사용자가 한다(생성 명령을 블록당 하나로 안내) |
| 3 | 레코드에 `args_hmac`과 키 식별자 `args_hmac_kid`를 같이 남겨 키 교체에 대비한다 |
| 4 | 키가 없거나 형식이 잘못되면 기존 감사 키 검사처럼 시작을 거부한다(`StartupError`). 조용히 해시 없이 기록하지 않는다 |
| 5 | 쓰기 도구의 기존 `draft_sha256`·`args_sha256`(무염)은 승인 결합과 동결 때문에 바꾸지 않는다. 읽기와 쓰기의 해시 방식이 다른 이유를 ADR에 적는다 |
| 6 | 한계 등록: 키를 가진 운영자는 추측 값을 대조할 수 있다. 키를 잃으면 과거 해시를 검증할 수 없다(키 백업은 사용자 몫) |
| 7 | tool_call ID 해시(`tool_call_sha256`)도 남겨, 7일 안에는 checkpoint의 원문 인자와 연결할 수 있게 한다 |

무염 sha256을 고르지 않은 이유: 검색어·티켓 번호처럼 경우의 수가 적은 인자는 사전 대입으로 되돌릴 수 있다. 감사 체인은 지우지 않는 반면 checkpoint 원문은 7일 뒤 삭제되므로(ADR-015), 무염 해시는 보존 원칙을 사실상 우회한다.

## 2. 실측: 감사 로그 계약

| 사실 | 근거 |
|---|---|
| `EVENTS`는 21개 이벤트의 `frozenset`이고 `tests/test_audit.py::test_events`가 정확한 집합으로 단언한다 | `audit/log.py:20-42`, `tests/test_audit.py:67` |
| 새 방식 이벤트(`service.start`·`model.call` 등)는 `_NEW_REQUIRED`(필수)·`_NEW_OPTIONAL`·`_NEW_FORMATS`(정규식)·`_NEW_ENUMS`로 필드를 고정하고 `_validate_new`가 검사한다 | `audit/log.py:154-250` |
| `_NEW_FIELDS`(새 방식 필드 합집합)는 **옛 방식 이벤트에서 금지**된다(`if _NEW_FIELDS & fields.keys(): raise`). `team`·`reason`만 빠져 있다 | `audit/log.py:180-183,256-257` |
| **충돌 실측**: 옛 이벤트 `side_effect.attempt`·`result`가 `args_sha256`을 쓰고(`tools/effects.py:51`), `guardrail.tool_quarantined`가 `tool_call_sha256`을 쓴다(`audit/log.py:100`, `graph.py:608-618`). 새 이벤트가 같은 이름을 쓰면 `_NEW_FIELDS`에서 빼야 한다. stub 시뮬레이션에서 빼지 않았을 때 228개 테스트가 `audit_write_failed`로 실패했다 | 4절 |
| 옛 방식 필드 이름 금지 목록 `_FORBIDDEN`에 `args`·`text`·`body`·`content`가 있다. `args_hmac`은 정확히 `args`가 아니므로 허용된다 | `audit/log.py:108-126` |
| 옛 방식 문자열 값은 형식이 맞지 않으면 `find_pii`를 돌린다. 새 방식(`_validate_new`)은 정규식·enum만 쓰고 PII 검사를 하지 않는다. 해시·도구 이름을 새 방식 형식으로 두면 FP-010(무작위 hex가 PII 규칙에 걸림)을 피한다 | `audit/log.py:212-250,292` |
| `audit/verify.py`는 `_validate_event`를 그대로 불러 레코드를 검증한다. 새 이벤트를 `EVENTS`·`_NEW_REQUIRED`에 넣으면 `verify.py`는 바뀌지 않는다 | `audit/verify.py:19,208` |
| 감사 append 비용(합성, 임시 키, 200건): 중앙값 0.15 ms, p95 0.22 ms, 최대 0.81 ms(macOS, 서명·`flock`·`fsync` 포함). 공식 N2 120 실행의 읽기 호출 215건 기준으로 실행당 평균 약 1.8건이라 지연 영향은 무시할 수준이다 | 이 세션 실측 |

## 3. 실측: 도구 호출 경로와 감사 대응

`route_model`(`graph.py:941-948`): 상태가 `running`이 아니면 끝, 쓰기 프로필이고 tool_call 중 하나라도 쓰기 도구면 `propose`, 아니면 `read_tools`.

| 경로 | 지금 감사 | 결정 | 근거 |
|---|---|---|---|
| `read_tools_body` 실행 성공 | 없음 | `tool.call` `status=ok`, `result_count=len(result_ids)` | `graph.py:550-560` |
| 실행했으나 도구 오류 결과(`is_error`) | 없음 | `tool.call` `status=tool_error` | `graph.py:556-559` |
| allowlist 거부(이름 없는 도구, 읽기 전용 프로필의 쓰기 도구 포함) | 없음 | `tool.call` `status=tool_not_available` | `graph.py:539-540,561-562` |
| 인자 JSON 아님·dict 아님·신원 필드 포함 | 없음 | `tool.call` `status=invalid_argument` | `graph.py:541-548` |
| 도구 예외(타임아웃·서킷 등) | 없음 | `tool.call` `status=tool_unavailable` | `graph.py:563-564` |
| `propose`: 호출 2개 이상(쓰기+읽기 혼합 포함)·이미 초안 있음·스키마 오류 → 모든 호출에 `invalid_argument` 응답 | 없음 | 각 호출에 `tool.call` `status=proposal_invalid` | `graph.py:677-702` |
| `propose`: 올바른 쓰기 호출 1개 | `side_effect.draft`(`tool`, `draft_sha256`) | **기존 기록이 덮는다**(`tool.call` 추가 안 함) | `graph.py:703-705` |
| `check`에서 정책·범위 차단(T3-05 `scope_wider_than_evidence`) | `side_effect.draft`(`check_result=blocked`, `reason`) | 기존 기록이 덮는다(`propose`의 초안 기록 뒤에 `check`가 다시 기록) | `graph.py:734-761`, M8 ① 결과 §2-1 |
| 마무리 단계(`steps ≥ MAX_STEPS`)에서 모델이 또 tool_call을 냄 → `failed/max_steps`, 실행 안 함 | 없음 | 각 호출에 `tool.call` `status=not_executed` | `graph.py:508-511` |

완전성 원칙: 조립된 서비스에서 모델이 낸 tool_call 하나마다 감사 레코드가 하나 이상 남는다(올바른 쓰기 제안은 `side_effect.draft`, 그 밖의 모든 경우는 `tool.call`). `propose`가 초안 감사 기록에 실패하면 task가 `audit_write_failed`로 끝나며, 이 경우는 기존 fail-closed 규칙이 이미 다룬다.

trace와의 대조: `read_tools_body`의 각 호출은 `SpanKind.TOOL_CALL` 도구 이름 span 하나를 만든다(`graph.py:530`). trace의 `tool_call` 종류에는 `tool_attempt` span도 섞여 있다(M8 ① 실측). 그래서 대조 규칙은 "턴마다 `tool.call` 중 `status ∈ {ok, tool_error, tool_not_available, invalid_argument, tool_unavailable}`의 개수 = trace의 `kind == "tool_call"`이고 `name != "tool_attempt"`인 span 수"다. `proposal_invalid`·`not_executed`는 span이 없어 대조에서 뺀다.

## 4. 실측: 소비자 (FP-071 시뮬레이션)

`git clone --local` 복사본(`.env`·`data/interim`·`data/raw` 없음 확인)에 최소 stub을 넣고 전체 pytest를 돌렸다. stub은 `EVENTS`에 `tool.call`을 더하고, 감사가 있으면 위 세 경로에서 기록한다.

- 1차(`args_sha256`을 `_NEW_FIELDS`에서 빼지 않음): 228 failed. 원인은 2절의 필드 이름 충돌(stub 결함). 골든 테스트도 이 때문에 실패했다.
- 2차(충돌 제거): 9 failed, 5226 passed. 이 중 `tests/test_faults_client.py` 3건은 stub 없이도 복제본에서 실패해 복제본 환경 탓이다. 실제 소비자는 아래 4곳이다.

| 소비자 | 단언 | 이 설계에서 |
|---|---|---|
| `tests/test_audit.py::test_events`(67행) | `EVENTS ==` 정확한 집합 | `"tool.call"` 한 항목 추가 |
| `tests/test_m7_freeze.py::test_digests_match_expected` | `EXPECTED` | `src/govops/agent`·`src/govops/audit/log.py` 두 키만 갱신 |
| `tests/test_agent_graph.py::test_invalid_write_proposals_return_model_error_without_effect`(581행) | 이벤트 목록 정확 일치 | 아래 주입 방식에서는 영향 없음(그래프를 직접 조립, 기록기 없음) |
| `tests/test_agent_semantic.py::test_off_preserves_exact_messages_arguments_checkpoint_and_audit`(155행) | 감사 레코드 정확 일치 | 같은 이유로 영향 없음 |

골든·재생 테스트(`test_golden_m3`·`test_regolden`·`test_golden_trace`)와 평가·장애 주입 러너 테스트는 2차에서 수정 없이 통과했다.

**주입 방식(결정)**: 그래프는 `build_graph(..., tool_audit=None)`로 기록기를 받는다. 기록기가 없으면 `tool.call`을 남기지 않는다. 기록기는 `build_task_service`만 만든다(키가 없으면 시작 거부). 그래서 운영 서비스·평가 러너·롤아웃 서비스에서는 항상 기록하고, 그래프를 직접 조립하는 단위 테스트는 기존 단언을 그대로 지킨다. 기록기 없이 감사만 있는 그래프가 "해시 없이 기록"하는 일은 없다(결정 4).

추가로 grep으로 찾은 소비자:

| 소비자 | 영향 |
|---|---|
| `tests/conftest.py` | 모든 테스트에 합성 HMAC 키 환경 변수를 넣는 autouse 픽스처가 필요하다(`build_task_service`가 키를 요구) |
| `tests/e2e_backend.py`(`REAL_ENV` 57~66행, 주입 환경 228~234행) | 실환경 거부 목록에 키 변수를 더하고 합성 키를 주입 |
| `tests/test_e2e_isolation.py` | `REAL_ENV`·주입 환경을 검사하므로 함께 갱신 |
| `.env.example` | 변수 이름 선언(AGENTS CRITICAL) |
| `audit/records.py` `ALLOWED_EVENT_FIELDS`(22~33행)·`_ENUMS`(37~43행) | 화면 투영. 새 필드를 넣지 않으면 화면에 이벤트 이름만 보인다 |
| `web/src/lib/messages.ts` `auditEventLabel`(52~59행) | 모르는 이벤트는 코드 그대로(`tool.call`) 표시 |
| `tests/test_web_sync.py`(21~30행) | `toolLabel`·`sentResultLabel` 표의 키 집합이 도구 명세와 정확히 같아야 한다. `unknown` 라벨은 표에 넣지 않고 화면 컴포넌트에서 처리 |
| `web/src/lib/server/project.ts`(128~137행) | BFF도 허용 목록으로 투영하므로 새 필드를 더해야 화면에 보인다(ADR-028) |
| `tests/e2e_backend.py`·`tests/test_e2e_backend.py` | 두 파일의 `REAL_ENV` 사본이 같아야 하고 이름별 거부 테스트가 있다 |
| `audit/records.py::read_events`의 `limit=500` | 대화당 표시 이벤트가 늘어 잘림(`truncated`)이 더 잦아진다(한계로 등록) |
| `eval/redteam_judge.py` `event_order` | 이벤트 이름으로 찾기만 해서 영향 없음 |
| `audit/verify.py` | 영향 없음(2절) |

## 5. 레코드 형식 (`tool.call`)

새 방식 이벤트(`_NEW_REQUIRED`)로 둔다. 원문 인자·결과 본문은 남기지 않는다(ADR-012).

| 필드 | 형식 | 의미 |
|---|---|---|
| `tool_name` | enum: 모든 프로필의 도구 이름 9개 + `unknown` | 모델이 낸 이름이 알려진 이름이면 그대로, 아니면 `unknown`(모델이 지어낸 문자열을 남기지 않음) |
| `status` | enum: `ok`·`tool_error`·`tool_not_available`·`invalid_argument`·`tool_unavailable`·`proposal_invalid`·`not_executed` | 3절 표 |
| `result_count` | 0 이상 정수(상한 10000) | 결과 ID 수, 실행되지 않았으면 0 |
| `tool_call_sha256` | `[0-9a-f]{64}` | tool_call ID의 sha256(`guardrail.tool_quarantined`와 같은 방식, `graph.py:608`) |
| `args_hmac` | `[0-9a-f]{64}` | HMAC-SHA256(키, 정규화 입력) |
| `args_hmac_kid` | `[0-9a-f]{12}` | 키 식별자 |

- `_NEW_FIELDS`에서 `tool_call_sha256`을 뺀다(옛 이벤트 `guardrail.tool_quarantined`가 쓰므로). `args_hmac`·`args_hmac_kid`·`tool_name`·`status`·`result_count`는 옛 이벤트가 쓰지 않음을 grep으로 확인했다.
- 정규화 입력: 인자 문자열이 JSON dict로 읽히면 `json.dumps({"tool": <원래 이름>, "args": <dict>}, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)`(`draft_sha256`과 같은 정규화, `agent/draft.py:44-52`). 읽을 수 없으면 `"raw\0" + <원래 이름> + "\0" + <원문 문자열>`(문자열이 아닌 인자는 `"raw-nonstr\0" + <원래 이름>`). JSON 정규화와 원문 경로는 앞부분 구분 표시로 충돌하지 않는다.
- 키: 환경 변수 `AUDIT_ARGS_HMAC_KEY`, 64자리 hex(32바이트). `kid = sha256(b"govops-args-hmac\0" + key_bytes).hexdigest()[:12]`. 이름에 `KEY`가 있어 `register_secret_values`가 trace·로그에서 가린다(`api/wiring.py:135-139`). 키는 state·checkpoint·trace·로그·LLM 컨텍스트에 두지 않는다(AGENTS CRITICAL).
- 키 검사: 없음 → `StartupError("audit_args_hmac_key_missing")`, 형식 오류(hex 64자 아님) → `StartupError("audit_args_hmac_key_invalid")`. 감사 서명 키와 같은 값이면 → `StartupError("audit_args_hmac_key_reused")`(재사용 금지, 서명 키의 raw 32바이트와 비교만 하고 출력하지 않음).
- 예외 경로(실측): 감사 서명 키 오류 `AuditKeyError`는 코드 목록 `_KEY_CODES`가 동결 파일 `audit/crypto.py`에 고정돼 새 코드를 더할 수 없다(`audit/crypto.py:39-46`). 그래서 새 모듈 `audit/argmac.py`가 자체 예외 `ArgsMacKeyError(code)`를 두고, 동결 밖인 `api/wiring.py::build_task_service`가 이를 `StartupError(code)`로 바꾼다(기존 `except AuditKeyError` 옆, `api/wiring.py:90-91`).

## 6. 실패 처리

읽기는 부작용이 아니므로 `model.call` 선례(`graph.py:458-466`: `asyncio.to_thread(audit.append, …)`, 실패 시 trace 속성 `model.audit_gap`)를 따른다. `tool.call` 기록 실패는 도구 실행·응답을 막지 않고 trace에 `tool.audit_gap=True`를 남긴다. ADR-012의 fail-closed는 부작용 의도 기록에 대한 것이고, 읽기 기록을 fail-closed로 하면 감사 디스크 장애가 읽기 전용 응답까지 막는다(가용성 손실 대비 이득이 없음). `propose` 경로의 `proposal_invalid` 기록도 같은 fail-open이다(부작용이 실행되지 않는 경로).

## 7. 동결 경계

| 동결 키(`tests/test_m7_freeze.py`) | 이전 digest | 바뀌는 이유 |
|---|---|---|
| `src/govops/agent` | `f801700264f23227445cc0dd1d248da52a2af9708b004ba2341e9bc598c9d58e` | `graph.py`에 기록 지점 3곳과 `build_graph(tool_audit=…)` |
| `src/govops/audit/log.py` | `014309c3b447ef2e34af338b53e39f1ef77d99fd5945cef03ea070f12acbf22d` | `tool.call` 이벤트·필드 형식 |

- 바뀌지 않는 동결 파일: `audit/verify.py`·`audit/smoke.py`·`audit/crypto.py`·`audit/anchor.py`·`audit/__init__.py`, `tools/`, `gateway/`, `guardrails/`, `eval/`, `evalrun/`, `data/*`, `infra/litellm/config.yaml`. 새 파일 `audit/argmac.py`·`audit/toolcall.py`는 동결 목록 밖이다.
- 1-3절 절차의 보완: "새 digest를 먼저 등록"은 변경 뒤에만 알 수 있다. 그래서 등록 문서(`hypotheses-m8-tool-audit.md`)에는 대상·이전 digest·사유만 적어 코드보다 먼저 커밋한다. 새 digest는 코드를 바꾸는 step이 `EXPECTED`의 해당 키만 갱신하면서 정해지고, 결과 문서의 "digest 변경 이력" 표에 적는다. 다른 키 불변은 `test_digests_match_expected`가 그대로 확인한다.
- **공식 구성 불변 증거**: `service.start`의 해시는 바뀌지 않는다. `hook_code_digest`의 대상 `HOOK_FILES`는 `gateway/`·`guardrails/`·`obs/` 일부와 `data/seed/users.json`이고 `graph.py`·`audit/log.py`를 포함하지 않는다(`gateway/hook_digest.py:9-23`). `policy_sha256`·`rules_sha256`·`access_index_sha256`의 대상 파일도 바뀌지 않는다. 골든·재생 테스트는 4절 시뮬레이션에서 수정 없이 통과했다.
- ADR: ADR-001~024 본문은 `tests/test_docs_consistency.py::test_m6_decision_contract`가 해시로 고정한다(505~512행). 그래서 ADR-012를 고치지 않고 새 **ADR-029**로 범위를 넓힌다.

## 8. 화면 노출

- 기존 원칙: 화면은 허용 필드만 보이고 `draft_hash`는 앞 12자만 보인다(`audit/records.py:103-108`). 전체 해시는 보이지 않는다.
- `tool.call`은 `tool_name`(라벨), `status`(라벨), `result_count`만 보인다. `args_hmac`·`args_hmac_kid`·`tool_call_sha256`은 보이지 않는다.
- 웹 라벨: 이벤트 "도구 호출", 상태 라벨 7개.

## 9. phase 구성 (`phases/m8-tool-audit/`)

| step | 이름 | 내용 |
|---|---|---|
| 0 | `tool-audit-registration` | 사전 등록 `hypotheses-m8-tool-audit.md`(동결 대상·이전 digest·사유·해시·실패 처리·완전성·대조 규칙), ADR-029, `test_m8_freeze` 키 |
| 1 | `audit-tool-event` | `audit/log.py`의 `tool.call` 형식, `audit/argmac.py`(키 읽기·kid·HMAC), `audit/toolcall.py`(기록기), `test_audit::test_events`·`test_m7_freeze`(`audit/log.py` 키) |
| 2 | `graph-tool-audit` | `graph.py` 기록 지점 3곳과 `tool_audit` 인자, `test_m7_freeze`(`src/govops/agent` 키) |
| 3 | `wiring-args-key` | `build_task_service`의 키 검사·기록기 주입, `conftest` 합성 키, e2e 백엔드, `.env.example` |
| 4 | `records-tool-view` | 서버 화면 투영(`audit/records.py`): 도구 이름·상태·결과 수 표시, 해시 비노출 |
| 5 | `web-tool-view` | BFF 투영·타입·라벨·기록 화면 표시 |
| 6 | `toolcall-check` | 사용자 실행 대조 CLI(`tool.call` 개수 = trace 도구 span 수)와 Make 타깃 |

## 10. 사용자 실행 (머지 후, 블록당 한 명령)

1. 키 생성(값을 화면에 출력하지 않고 `.env`에 덧붙임). 줄바꿈을 먼저 붙여 `.env` 마지막 줄 끝에 줄바꿈이 없어도 새 줄에 들어가게 한다(정정 2026-10-21: 처음 명령은 줄바꿈 없는 마지막 줄 뒤에 키가 이어 붙었다, 결과 문서 참조):

```
python3 -c "import secrets; print('\nAUDIT_ARGS_HMAC_KEY=' + secrets.token_hex(32))" >> .env
```

2. 키가 한 번만 들어갔는지 확인(값은 보지 않음):

```
grep -c '^AUDIT_ARGS_HMAC_KEY=' .env
```

3. 서버 기동 뒤 대화 smoke(실제 호출, 센트 단위 비용):

```
make smoke-conversation
```

4. 감사 체인 검증:

```
make audit-verify
```

5. 그 실행의 `tool.call` 개수와 trace 도구 span 수 대조:

```
make audit-toolcall-check
```

키 백업은 사용자 몫이다. 키를 잃으면 과거 `args_hmac`을 검증할 수 없다(결정 6).

## 11. 한계 (등록 대상)

- 키를 가진 운영자는 추측 값을 대조할 수 있다(HMAC은 키 보유자에 대한 보호가 아니다).
- 키를 잃으면 과거 해시를 검증할 수 없다. 키를 교체하면 `args_hmac_kid`가 바뀌고, 옛 kid의 레코드는 옛 키로만 검증된다.
- 읽기와 쓰기의 인자 해시 방식이 다르다(읽기 HMAC, 쓰기 무염 sha256). 쓰기의 무염 해시는 승인 결합과 동결 때문에 유지한다(결정 5).
- 그래프를 직접 조립한 경로(단위 테스트)에서는 `tool.call`을 남기지 않는다. 완전성은 `build_task_service`로 조립한 서비스에서만 보장한다.
- `tool.call` 기록은 fail-open이다. 감사 쓰기 실패 시 trace의 `tool.audit_gap`으로만 남는다.
- 대화 화면의 이벤트 표시 상한 500개에 더 빨리 도달한다.
- 이 변경 전의 실행(공식 M6·M8 측정)의 감사 체인에는 `tool.call`이 없다.
