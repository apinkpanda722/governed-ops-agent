# M8 감사 체인 도구 호출 사전 등록 (`hypotheses-m8-tool-audit.md`)

**M8 추가 — 공식 M6 수치 아님** · `scope: "m8"`

기준 설계: [`M8_TOOL_AUDIT_DRAFT.md`](../M8_TOOL_AUDIT_DRAFT.md). 이 문서는 코드 변경보다 먼저 커밋하며, 커밋 뒤 본문을 수정하지 않는다(FP-043). `tests/test_m8_freeze.py`가 digest를 고정한다. 사용자 결정 2026-10-21(지시 47). 공식 M6 수치는 변경 전 코드로 낸 값이고 바꾸지 않는다.

## 1. 범위와 사용자 결정

지금 감사 체인은 쓰기 도구만 남긴다(`side_effect.draft`의 `draft_sha256`, `side_effect.attempt`·`result`의 `args_sha256`). 읽기 도구 호출은 trace span(`tool.result.ids`)에만 있다. ADR-012가 감사 범위를 "책임 추적과 보안·부작용 사건"으로 정했을 뿐 읽기를 뺀다는 결정은 따로 없었다. 이 작업은 모델이 낸 모든 tool_call을 위변조 탐지 가능한 감사 체인에 남긴다.

사용자 결정(2026-10-21):

| # | 결정 |
|---|---|
| 1 | 동결 경계 변경 승인(지시 47) |
| 2 | 읽기 호출 인자는 **HMAC-SHA256**으로 기록한다. 감사 서명 키(Ed25519)와 분리된 전용 키를 쓰고(재사용 금지), 키는 `.env`에만 두며 생성은 사용자가 한다(생성 명령을 블록당 하나로 안내) |
| 3 | 레코드에 `args_hmac`과 키 식별자 `args_hmac_kid`를 같이 남겨 키 교체에 대비한다 |
| 4 | 키가 없거나 형식이 잘못되면 기존 감사 키 검사처럼 시작을 거부한다(`StartupError`). 조용히 해시 없이 기록하지 않는다 |
| 5 | 쓰기 도구의 기존 `draft_sha256`·`args_sha256`(무염)은 승인 결합과 동결 때문에 바꾸지 않는다. 읽기와 쓰기의 해시 방식이 다른 이유를 ADR에 적는다 |
| 6 | 한계 등록: 키를 가진 운영자는 추측 값을 대조할 수 있다. 키를 잃으면 과거 해시를 검증할 수 없다(키 백업은 사용자 몫) |
| 7 | tool_call ID 해시(`tool_call_sha256`)도 남겨, 7일 안에는 checkpoint의 원문 인자와 연결할 수 있게 한다 |

## 2. 완전성 원칙과 경로 대응 표

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

완전성 원칙: `build_task_service`로 조립한 서비스에서 모델이 낸 tool_call 하나마다 감사 레코드가 하나 이상 남는다(올바른 쓰기 제안은 `side_effect.draft`, 그 밖의 모든 경우는 `tool.call`). 올바른 쓰기 제안과 `check` 차단은 기존 `side_effect.draft`가 덮는다. `propose`가 초안 감사 기록에 실패하면 task가 `audit_write_failed`로 끝나며, 이 경우는 기존 fail-closed 규칙이 이미 다룬다.

그래프는 `build_graph(..., tool_audit=None)`로 기록기를 받는다. 기록기가 없으면 `tool.call`을 남기지 않는다. 기록기는 `build_task_service`만 만든다(키가 없으면 시작 거부). 운영 서비스·평가 러너·롤아웃 서비스에서는 항상 기록하고, 그래프를 직접 조립하는 단위 테스트는 기존 단언을 그대로 지킨다. 기록기 없이 감사만 있는 그래프가 해시 없이 기록하는 일은 없다.

## 3. 레코드 형식

이벤트는 `tool.call`이며 새 방식 이벤트(`_NEW_REQUIRED`)로 둔다. 원문 인자·결과 본문은 남기지 않는다(ADR-012).

| 필드 | 형식 | 의미 |
|---|---|---|
| `tool_name` | enum 10개: `get_document`·`jira_add_comment`·`jira_create_followup_ticket`·`jira_get_ticket`·`jira_search_tickets`·`lookup_id`·`run_sql`·`search_docs`·`slack_send_message`·`unknown` | 모델이 낸 이름이 알려진 이름이면 그대로, 아니면 `unknown`(모델이 지어낸 문자열을 남기지 않음) |
| `status` | enum 7개: `ok`·`tool_error`·`tool_not_available`·`invalid_argument`·`tool_unavailable`·`proposal_invalid`·`not_executed` | 2절 표 |
| `result_count` | 0~10000 정수 | 결과 ID 수, 실행되지 않았으면 0 |
| `tool_call_sha256` | `[0-9a-f]{64}`(64 hex) | tool_call ID의 sha256(`guardrail.tool_quarantined`와 같은 방식, `graph.py:608`) |
| `args_hmac` | `[0-9a-f]{64}`(64 hex) | HMAC-SHA256(키, 정규화 입력) |
| `args_hmac_kid` | `[0-9a-f]{12}`(12 hex) | 키 식별자 |

`_NEW_FIELDS`는 새 방식 필드 합집합이며 옛 방식 이벤트에서는 금지된다. `tool_call_sha256`은 옛 이벤트 `guardrail.tool_quarantined`가 쓰므로 `_NEW_FIELDS`에서 뺀다. `args_sha256`은 옛 이벤트 `side_effect.attempt`·`result`가 쓰므로 새 이벤트에는 쓰지 않는다. `args_hmac`·`args_hmac_kid`·`tool_name`·`status`·`result_count`는 옛 이벤트가 쓰지 않는다. 해시·도구 이름은 새 방식의 정규식·enum으로 검증해 무작위 hex가 PII 규칙에 걸리는 FP-010을 피한다.

## 4. 해시 방식

HMAC-SHA256을 쓴다. 정규화 입력은 다음과 같으며 모두 UTF-8로 인코딩한다.

- 인자가 JSON dict이면 `json.dumps({"tool": <원래 이름>, "args": <dict>}, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)`.
- JSON dict로 읽을 수 없는 문자열 인자는 `"raw\0" + <원래 이름> + "\0" + <원문>`.
- 문자열이 아닌 인자는 `"raw-nonstr\0" + <원래 이름>`.

JSON 정규화와 원문 경로는 앞부분 구분 표시로 충돌하지 않는다. 도구 이름은 해시 입력에 원래 이름을 쓰고, 레코드의 `tool_name`에만 알려진 이름 또는 `unknown`을 쓴다.

키는 환경 변수 `AUDIT_ARGS_HMAC_KEY`이며 64자리 hex, 32바이트다. 감사 서명 키(Ed25519)와 분리된 전용 키를 쓰며 재사용하지 않는다. `args_hmac_kid = sha256(b"govops-args-hmac\0" + key_bytes).hexdigest()[:12]`다. 키는 `.env`에만 두고 사용자가 생성한다. 이름에 `KEY`가 있어 `register_secret_values`가 trace·로그에서 가린다. 키는 state·checkpoint·trace·로그·LLM 컨텍스트에 두지 않는다.

무염 sha256을 고르지 않은 이유: 검색어·티켓 번호처럼 경우의 수가 적은 인자는 사전 대입으로 되돌릴 수 있다. 감사 체인은 지우지 않는 반면 checkpoint 원문은 7일 뒤 삭제되므로(ADR-015), 무염 해시는 보존 원칙을 사실상 우회한다. 전용 키 HMAC은 키가 없는 사람의 사전 대입을 막는다. 키를 가진 운영자는 추측 값을 대조할 수 있고, 키를 잃으면 과거 해시를 검증할 수 없다. `args_hmac_kid`를 함께 남겨 키 교체에 대비하고 `tool_call_sha256`으로 7일 안에는 checkpoint의 원문 인자와 연결한다(사용자 결정 2~7).

읽기는 HMAC, 쓰기는 기존 무염 `draft_sha256`·`args_sha256`을 유지한다. 쓰기의 해시는 승인 결합과 동결 때문에 바꾸지 않는다(사용자 결정 5).

## 5. 키 검사와 시작 거부

| 조건 | 코드 |
|---|---|
| 키 없음 | `audit_args_hmac_key_missing` |
| 형식 오류(hex 64자 아님) | `audit_args_hmac_key_invalid` |
| 감사 서명 키의 raw 32바이트와 같음(비교만 하고 출력하지 않음) | `audit_args_hmac_key_reused` |

`audit/argmac.py`의 자체 예외 `ArgsMacKeyError(code)`를 `api/wiring.py::build_task_service`가 `StartupError(code)`로 바꾼다. 감사 서명 키 오류 `AuditKeyError`의 코드 목록 `_KEY_CODES`는 동결 파일 `audit/crypto.py`에 고정돼 있으므로 새 코드를 더하지 않는다. 키가 없거나 잘못돼도 조용히 해시 없이 기록하지 않는다.

## 6. 실패 처리

`tool.call` 기록은 fail-open이다. 읽기는 부작용이 아니므로 `model.call` 선례(`asyncio.to_thread(audit.append, …)`, 실패 시 trace 속성 `model.audit_gap`)를 따른다. 기록 실패는 도구 실행·응답을 막지 않고 trace 속성 `tool.audit_gap=True`를 남긴다.

ADR-012의 fail-closed는 부작용 의도 기록에 대한 것이다. 읽기 기록을 fail-closed로 하면 감사 디스크 장애가 읽기 전용 응답까지 막는다(가용성 손실 대비 이득이 없음). `propose` 경로의 `proposal_invalid` 기록도 부작용이 실행되지 않는 경로이므로 같은 fail-open이다.

## 7. 동결 경계

| 동결 키(`tests/test_m7_freeze.py`) | 이전 digest | 바뀌는 이유 |
|---|---|---|
| `src/govops/agent` | `f801700264f23227445cc0dd1d248da52a2af9708b004ba2341e9bc598c9d58e` | `graph.py`에 기록 지점 3곳과 `build_graph(tool_audit=…)` |
| `src/govops/audit/log.py` | `014309c3b447ef2e34af338b53e39f1ef77d99fd5945cef03ea070f12acbf22d` | `tool.call` 이벤트·필드 형식 |

새 digest는 변경 뒤에만 알 수 있으므로 이 문서에는 적지 않는다. 이 문서는 대상·이전 digest·사유만 코드보다 먼저 등록한다. 코드를 바꾸는 step이 `tests/test_m7_freeze.py`의 `EXPECTED` 해당 키만 갱신하고, 결과 문서의 "digest 변경 이력" 표에 새 digest를 적는다. 다른 키는 그대로임을 `test_digests_match_expected`가 확인한다. 이는 `M8_DESIGN_DRAFT.md` 1-3절의 "새 digest를 먼저 등록" 절차를 보완한다.

바뀌지 않는 동결 파일은 `audit/verify.py`·`audit/smoke.py`·`audit/crypto.py`·`audit/anchor.py`·`audit/__init__.py`, `tools/`, `gateway/`, `guardrails/`, `eval/`, `evalrun/`, `data/*`, `infra/litellm/config.yaml`이다. 새 파일 `audit/argmac.py`·`audit/toolcall.py`는 동결 목록 밖이다.

공식 구성 불변 증거: `service.start`의 해시는 바뀌지 않는다. `hook_code_digest`의 대상 `HOOK_FILES`는 `gateway/`·`guardrails/`·`obs/` 일부와 `data/seed/users.json`이고 `graph.py`·`audit/log.py`를 포함하지 않는다(`gateway/hook_digest.py:9-23`). `policy_sha256`·`rules_sha256`·`access_index_sha256`의 대상 파일도 바뀌지 않는다. 골든·재생 테스트(`test_golden_m3`·`test_regolden`·`test_golden_trace`)는 초안 4절 시뮬레이션에서 수정 없이 통과했다. 구현 뒤에도 무수정 통과를 확인하고 사용자 실행에서 `service.start` 해시를 대조한다. M6 결과 문서는 수정하지 않는다.

ADR-001~024 본문은 `tests/test_docs_consistency.py::test_m6_decision_contract`가 해시로 고정한다. ADR-012를 고치지 않고 ADR-029로 범위를 넓히며 ADR-001~028은 보존한다.

## 8. trace 대조 규칙

턴마다 `tool.call` 중 `status ∈ {ok, tool_error, tool_not_available, invalid_argument, tool_unavailable}`의 개수 = trace의 `kind == "tool_call"`이고 `name != "tool_attempt"`인 span 수다. `proposal_invalid`·`not_executed`는 span이 없어 대조에서 뺀다.

CLI는 `python -m govops.audit.toolcall_check`이며 Make 타깃은 `make audit-toolcall-check`다. 출력은 다음 둘 중 하나다.

```text
PASS audit_toolcall_check turns=<n> tool_calls=<n> spans=<n>
```

```text
FAIL audit_toolcall_check <코드>
```

실패 코드는 `count_mismatch`·`no_turns`·`trace_missing`·`audit_unavailable`이다.

## 9. 화면 노출

- 기존 원칙: 화면은 허용 필드만 보이고 `draft_hash`는 앞 12자만 보인다(`audit/records.py:103-108`). 전체 해시는 보이지 않는다.
- `tool.call`은 `tool_name`(라벨), `status`(라벨), `result_count`만 보인다. `args_hmac`·`args_hmac_kid`·`tool_call_sha256`은 보이지 않는다.
- 웹 라벨: 이벤트 "도구 호출", 상태 라벨 7개.

## 10. 고정 코드 목록 (단일 출처)

| 구분 | 코드 |
|---|---|
| 이벤트 | `tool.call` |
| status | `ok`·`tool_error`·`tool_not_available`·`invalid_argument`·`tool_unavailable`·`proposal_invalid`·`not_executed` |
| 키 오류 | `audit_args_hmac_key_missing`·`audit_args_hmac_key_invalid`·`audit_args_hmac_key_reused` |
| trace 속성 | `tool.audit_gap` |
| 대조 CLI | `count_mismatch`·`no_turns`·`trace_missing`·`audit_unavailable` |

## 11. 한계

- 키를 가진 운영자는 추측 값을 대조할 수 있다(HMAC은 키 보유자에 대한 보호가 아니다).
- 키를 잃으면 과거 해시를 검증할 수 없다. 키를 교체하면 `args_hmac_kid`가 바뀌고, 옛 kid의 레코드는 옛 키로만 검증된다.
- 읽기와 쓰기의 인자 해시 방식이 다르다(읽기 HMAC, 쓰기 무염 sha256). 쓰기의 무염 해시는 승인 결합과 동결 때문에 유지한다(결정 5).
- 그래프를 직접 조립한 경로(단위 테스트)에서는 `tool.call`을 남기지 않는다. 완전성은 `build_task_service`로 조립한 서비스에서만 보장한다.
- `tool.call` 기록은 fail-open이다. 감사 쓰기 실패 시 trace의 `tool.audit_gap`으로만 남는다.
- 대화 화면의 이벤트 표시 상한 500개에 더 빨리 도달한다.
- 이 변경 전의 실행(공식 M6·M8 측정)의 감사 체인에는 `tool.call`이 없다.

## 12. 사용자 실행

머지 후 사용자가 실행한다. 블록당 한 명령이다.

1. 키 생성(값을 화면에 출력하지 않고 `.env`에 덧붙임):

```
python3 -c "import secrets; print('AUDIT_ARGS_HMAC_KEY=' + secrets.token_hex(32))" >> .env
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
