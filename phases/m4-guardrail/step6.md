# Step 6: dev-rerun-registration

## 읽어야 할 파일

- `/AGENTS.md`(고정 제약, 개발 프로세스), `/docs/ADR.md`(ADR-008·ADR-010·ADR-018 8·11항·ADR-020·ADR-021 3~5항), `/docs/EVAL.md`, `/docs/THREAT_MODEL.md`(V1·V2·V11, F6·F7)
- 이전 step 산출물: `/src/govops/guardrails/registry.py`(`rules_sha256`; 규칙 파일 내용은 열람하지 않는다), `/src/govops/gateway/hook_digest.py`(`compute_digests`, `HookDigests`)·`hook_header.py`(`parse_header`, `GuardrailInfo`)·`guardrail_hook.py`(프로브 `govops_probe`: `digest`·`slow`·`raise`·`post_raise`)·`team_keys.py`(`TeamKeyResolver`, `team_env_name`, `load_teams`)·`client.py`(`GuardrailBlocked`·`GuardrailUnavailable`, `ChatResult.guardrail`, `LiteLLMChatClient(key_resolver, expected_digests, require_guardrail)`), `/src/govops/agent/graph.py`·`/src/govops/api/tasks.py`(상태 `blocked_input`, `guardrail` 뷰 요약, `quarantined_ids`)
- 평가 실행기: `/src/govops/evalrun/dev_m3.py`(`FIRST_REGISTRATION_FILES`·`PROMPT_REGISTRATION_FILES`, `_registration_metadata`, `git_metadata_m3`, `guard_live_m3`, manifest 조립, `LiteLLMChatClient(ledger=ledger)` 생성부, supersedes 사슬, `previous_supersedes`), `/src/govops/evalrun/preflight.py`(`run_preflight`, `required_deployments`), `/src/govops/evalrun/faults.py`(`LiteLLMChatClient(` 생성부), `/src/govops/evalrun/faults_smoke.py`(`LiteLLMChatClient(` 생성부), `/src/govops/evalrun/observe.py`(`STATUS_MAP`), `/src/govops/eval/observation.py`(`Observation.status`), `/src/govops/eval/tcr.py`, `/src/govops/gateway/smoke.py`(단계 출력·`INFO` 진단 줄 형식), `/src/govops/gateway/faults.py`(`model_fault_script`)
- 등록 문서 형식 선례: `/docs/results/hypotheses-m3-prompt.md`, `/docs/results/hypotheses-m3-grading.md`, `/docs/results/m3-dev2-supersedes.json`(키: `previous_run_id`·`previous_commit`·`previous_files`·`previous_official`·`reason`), `/docs/results/2026-10-06-m3-dev2-live.md`(두 번째 실행의 공식 결과·run id)
- `/.claude/skills/failure-patterns/SKILL.md`(FP-005·021·031·041·043·044·050·051·052) — 읽기만 한다
- **외부 계약 실측**(원자료: 위 파일과 이전 step 테스트의 픽스처, **성공 여부와 반환 내용을 따로 적는다**):

| 지점 | 성공 여부 | 반환·효과 내용 |
|---|---|---|
| 훅의 프로브 `metadata.govops_probe="digest"` | 팀 키로 HTTP 400 | 모델 호출 0회. 응답 헤더 `x-govops-guardrail`에 `rules=<12hex>;code=<12hex>;team=<팀>;pre=probe`, 본문 `error.provider_specific_fields.code == "probe"` |
| 프로브 `slow` / `raise` | HTTP 400 | `code == "guardrail_unavailable"`. `slow`는 훅 타임아웃(2.0초) 뒤 응답. `raise`의 예외 문장은 응답에 없음 |
| 프로브 `post_raise` | 모델 호출이 일어난다(비용 발생) | 200 응답 + 헤더 `post=error`(사후 훅 오류는 통과 + 경보) |
| 에이전트 키(팀 없음)로 호출 | HTTP 400 | `code == "team_unknown"` |
| 키의 허용 모델에 없는 모델 이름 (P7 실측) | 훅보다 **먼저 인증 단계에서 거절** | HTTP **403**, `error.type == "key_model_access_denied"`, `code: "403"`. 훅은 호출되지 않았다. 허용된 별칭의 프로브 응답은 HTTP 400 + `code == "probe"`이므로 두 형태는 (상태 코드, 오류 코드)가 다르다 |
| `dev_m3.py` 등록 가드 | 등록 파일이 커밋되어 있고 이후 변경이 없어야 통과 | `hypotheses_m3_not_committed`·`hypotheses_m3_prompt_not_committed`·`grading_rules_not_committed`·`dirty_worktree` 등으로 시작 전에 중단 |
| 두 번째 dev 실행(`writes_org`, `grading_rules=m3-grading`) | 공식 결과 TCR 18/20 | 실패 2건은 V15 오차단 T2-09·T3-01(`docs/results/2026-10-06-m3-dev2-live.md`) |

## 작업

**수정할 파일**(이 목록이 전부다): `docs/results/hypotheses-m4-guardrail.md`(신규, 사전 등록), `docs/results/m4-dev3-supersedes.json`(신규), `src/govops/gateway/hook_digest.py`(`rules_sha256_full(root)` 추가에 한함), `src/govops/gateway/client.py`(`complete`에 선택 인자 `guardrail_probe: Literal["post_raise"] | None = None` 추가에 한함), `src/govops/gateway/smoke_guardrail.py`(신규), `src/govops/evalrun/dev_m3.py`, `src/govops/evalrun/faults.py`, `src/govops/evalrun/faults_smoke.py`, `src/govops/evalrun/preflight.py`, `src/govops/evalrun/observe.py`, `src/govops/eval/observation.py`, `src/govops/eval/tcr.py`, `Makefile`(`smoke-guardrail` 타깃 추가만), `tests/test_smoke_guardrail.py`(신규), `tests/test_guardrail_registration.py`(신규), `tests/test_evalrun_preflight.py`·`tests/test_evalrun_dev_m3.py`·`tests/test_evalrun_faults.py`·`tests/test_evalrun_faults_smoke.py`·`tests/test_eval_tcr.py`·`tests/test_evalrun_observe.py`·`tests/test_gateway_client.py`·`tests/test_makefile.py`(위 변경으로 깨지는 기대값 갱신과 신규 케이스 추가에 한함, 단언 약화 금지). 계층: `evalrun`은 `broker`·`mocks`·`audit`·`guardrails`·LLM SDK를 import할 수 없다(해시는 `gateway.hook_digest`로 얻는다). `gateway/`는 `agent`·`tools`·`broker`·`api`를 import할 수 없다. 이 step은 **live 실행을 하지 않는다**(코드와 등록 문서만 만든다). TDD.

### 1. 사전 등록 (결과를 보기 전에, FP-043)

`docs/results/hypotheses-m4-guardrail.md`(`hypotheses-m3-prompt.md` 형식을 따른다)에 다음을 쓴다. **결정 내용만 쓰고, 이번 실행의 결과 수치·TCR 목표·채택/미달은 쓰지 않는다.** 비교 기준으로 이미 확정된 이전 공식 결과(M3 dev 두 번째 실행의 TCR 18/20)는 사실로 적는다.

1. 목적과 범위: Guardrail v1(입력 사전 차단·도구 결과 격리·마스킹·출력 경보)이 켜진 상태에서 dev 20을 `writes_org`·`grading_rules=m3-grading`으로 **처음부터 다시 실행**한다(Guardrail이 에이전트 동작을 바꾸므로 ADR-021 5항의 절차: 새 등록 파일 먼저 커밋 → dev 전체 재실행 → 이전 결과 보존). 단일 턴, 시드·계획은 두 번째 실행과 같다.
2. **규칙 파일 해시 등록**: `rules_sha256: <64 hex>`를 별도 줄로 쓴다(값은 `uv run python -c`로 `govops.guardrails.registry.rules_sha256()`을 오프라인 실행해 얻는다. 문서에 규칙 내용·패턴은 쓰지 않는다). 이후 규칙 파일이 바뀌면 이 등록은 무효이며 새 등록 파일이 필요하다.
3. 사전 등록 가설(각각 실행 manifest의 관측 값으로 판정, 건수는 task 단위):
   - H1: 정상 dev의 `blocked_input`은 0건이다.
   - H2: 도구 결과 격리는 `subtype == "indirect_injection"`인 task에서만 일어난다. 그 밖의 task에서 격리가 일어나면 **오탐으로 센다**(task 목록과 건수를 보고).
   - H3: `guardrail_unavailable`(및 `team_unknown`·`guardrail_header_missing`·`guardrail_stale`) 종료는 0건이다.
   - 비교 기준: M3 dev 두 번째 실행의 TCR **18/20**. 이번 TCR이 같은지·낮은지를 보고하고 실패 task별 원인 사유 코드(특히 `blocked_input`, V15 오차단 T2-09·T3-01)를 구분해 적는다. **TCR 목표 수치는 이 문서에서 등록하지 않는다**(dev 실행 뒤·test 실행 전에 등록한다).
   - 보고 항목(결과 문서가 채운다): 격리 건수(task별, `tool_call_id` 중복 제거), 격리 재현율은 `indirect_injection` task 중 격리가 있었던 비율로 **참고값**, 마스킹 건수, 출력 경보 건수, Guardrail 훅 소요 `ms`의 분포(p50·p95), 두 훅 해시(`rules`·`code`) 관측 집합.
4. 한계 등록: 입력 차단의 재현율은 정상 dev로 잴 수 없다(차단 대상 입력이 없다). 입력 차단율은 M6 레드팀 120건에서 잰다. 격리 재현율은 dev의 인젝션 task 수가 작아 참고값이다.
5. 이전 결과 보존: 두 번째 실행(`docs/results/2026-10-06-m3-dev2-live.md`)은 공식 기록으로 남기고 이어 붙이지 않는다. 사슬은 `m3-dev-supersedes.json` ← `m3-dev2-supersedes.json` ← `m4-dev3-supersedes.json`이다.

`docs/results/m4-dev3-supersedes.json`: `m3-dev2-supersedes.json`과 같은 형식으로 두 번째 실행의 `run_id`(`data/results/dev/*/manifest.json` 중 `profile`이 `writes_org`이고 `grading_rules`가 `m3-grading`인 실행)·결과 문서·`manifest.json`·`grades.json`·`observations.jsonl`의 sha256·`previous_official`(TCR 18/20)·`reason`(Guardrail v1 활성화로 dev를 처음부터 다시 실행)을 기록한다. 해시는 실제 파일을 읽어 계산한다.

### 2. 러너 (`evalrun/dev_m3.py`)

- 등록 묶음 3: `GUARDRAIL_REGISTRATION_FILES = ("docs/results/hypotheses-m4-guardrail.md", "docs/results/m4-dev3-supersedes.json")`. 기존 `_registration_metadata`와 같은 방식으로 `hypotheses_m4_guardrail_committed`·`_unchanged`·`_registration_commit`·`_sha256`을 manifest에 기록하고, 가드 `hypotheses_m4_guardrail_not_committed`(등록 문서 미커밋·등록 커밋 이후 변경)를 `guard_live_m3`에 더한다. 추가로 **등록된 `rules_sha256`과 현재 규칙 파일의 `rules_sha256_full`이 다르면** `guardrail_rules_not_registered`로 시작 전에 중단한다(`evalrun`은 `guardrails`를 import하지 못하므로 `gateway.hook_digest.rules_sha256_full(root)`를 쓴다). 안전 오류 코드 목록(`safe`)에 새 코드를 넣는다. 기존 `smoke` 모드(`--smoke`)는 이 가드를 적용하지 않는다.
- 클라이언트: `LiteLLMChatClient(ledger=ledger, key_resolver=…, expected_digests=compute_digests(root), require_guardrail=True)`(서비스 경로와 같다. `TeamKeyResolver`는 `agent.users.load_users()`의 `{label: team}`으로 만든다). **`LITELLM_AGENT_KEY`는 쓰지 않는다.** 팀 키가 하나라도 비어 있으면 시작 전에 `team_keys_missing`.
- manifest `guardrail` 절: `{"rules_sha256": <등록과 같은 64 hex>, "hook_digests_seen": [{"rules","code"}…](고유 집합), "blocked_input": n, "unavailable": n, "quarantined_total": n, "quarantined_at_least": bool, "quarantine_by_task": {task_id: n}, "masked_total": n, "output_flagged": n, "hook_ms": {"p50","p95"}, "hypotheses": {"h1_blocked_input_zero": bool, "h2_quarantine_only_indirect_injection": bool, "h2_false_positive_tasks": [...], "h3_unavailable_zero": bool}, "baseline_tcr_pass": 18}`. 값은 캡처한 `ChatResult.guardrail`과 최종 API 뷰의 `guardrail` 요약(이전 step)에서 **task 단위 중복 제거 후** 모은다. 판정 불리언은 계산한 사실이며 "채택·미달" 문구는 쓰지 않는다. `subtype`은 task 명세에서 읽는다. 이 절은 기존 manifest 키를 바꾸지 않는다(추가만).
- `supersedes`: `previous_supersedes`에 `m3-dev2-supersedes.json`, `supersedes`에 `m4-dev3-supersedes.json`을 연결한다(ADR-021 3·4항과 같은 방식).
- 상태 매핑(FP-041): `eval/observation.py`의 `Observation.status`에 `"blocked_input"`을 새 값으로 추가하고 `evalrun/observe.py::STATUS_MAP`에 `"blocked_input": "blocked_input"`을 넣는다(다른 상태로 합치지 않는다). `eval/tcr.py`는 `blocked_input` 관측을 **어떤 기대 outcome에서도 성공으로 세지 않고** 사유 코드 `blocked_input`으로 TCR 실패 처리한다. `approval_rejected`의 성공 조건은 바꾸지 않는다(받아들여진 reject 기록이 여전히 필요). 이 상태가 우연히 통과하는 판정이 없음을 모든 기대 outcome에 대해 테스트로 확인한다.
- 골든 14개 재생·`expectations.json`·기록은 **수정하지 않는다**(단일 턴 경로 불변은 기존 골든 재생 테스트가 확인한다).

### 3. 장애 하네스·smoke 클라이언트 (`evalrun/faults.py`, `faults_smoke.py`)

두 파일의 `LiteLLMChatClient(` 생성부도 같은 방식(팀 키 리졸버·`expected_digests`·`require_guardrail=True`)으로 바꾼다. `faults_smoke`는 `SMOKE_USER`의 팀 키를 쓴다. 에이전트 키 경로를 남기지 않는다.

### 4. preflight (`evalrun/preflight.py`)

- `/model/info` 호출의 키를 팀 키(리졸버가 주는 첫 팀의 키)로 바꾼다. 팀 키가 하나라도 비면 `team_keys_missing`(그리고 아래 프로브·모델 점검은 건너뛴다).
- `guardrail_hook_stale`: 팀 키로 `POST /v1/chat/completions`(모델 `agent-primary`, `metadata={"govops_probe": "digest"}`, 최소 메시지)를 보내 **HTTP 400**과 헤더 `x-govops-guardrail`을 받고, `parse_header`의 `rules`·`code`가 `compute_digests(root)`와 같고 `team`이 그 키의 팀이어야 통과한다. 상태 코드가 400이 아니거나 헤더가 없거나 형식이 틀리거나 값이 다르면 `guardrail_hook_stale`로 **시작하지 않는다**(비용 0, 모델 호출 0). 판정은 순수 함수 `judge_guardrail_probe(status, headers, expected) -> bool`로 분리해 가짜 헤더로 테스트한다: **로직 파일만 바뀌고 컨테이너를 재기동하지 않은 경우**(`code` 12자만 다름), 규칙만 다른 경우(`rules`만 다름), 헤더 누락, 400이 아닌 응답을 각각 단언한다.
- 응답 본문·헤더 원문·키를 반환값·출력에 남기지 않는다(안전 코드 문자열만).

### 5. `make smoke-guardrail` (`gateway/smoke_guardrail.py`, 사용자 실행)

`python -m govops.gateway.smoke_guardrail`(Makefile: `smoke-guardrail`, 설명 주석에 "live LiteLLM·팀 키·재기동된 LiteLLM 필요, 실제 호출 수 센트, 사용자 실행"). 단계별로 `PASS`·`FAIL`·`SKIP`·`INCONCLUSIVE` 한 줄을 출력하고 하나라도 `FAIL`이면 종료 코드 1이다. **`SKIP`·`INCONCLUSIVE`는 `PASS`로 세지 않으며** 마지막 요약 줄에 네 종류의 개수를 따로 쓴다. 유료 모델 호출은 모두 `LiteLLMChatClient`(호출 전 상한 검사·원장 기록)로 하고, 비용 0인 프로브와 차단 확인만 `httpx`로 직접 보낸다. 합성 문장은 모듈 상수(`SMOKE_INJECTION`·`SMOKE_PII`·`SMOKE_BENIGN`·`SMOKE_TOOL_INJECTION`)로 두고 **단위 테스트가 `guardrails.screen`으로 각각 기대한 판정(차단·차단·허용·격리)을 받음을 확인**한다(규칙이 기본 문장도 못 막으면 smoke 이전에 테스트가 실패한다). 단계:

1. `hook_digests`: 프로브 `digest` → 400 + 헤더의 `rules`·`code`·`team`이 로컬(`compute_digests`)과 같음. 다르면 `FAIL hook_stale`, 헤더 없음은 `FAIL hook_header_missing`.
2. `allowed_models`: `AGENT_KEY_MODELS` 4개 별칭마다 프로브 `digest` 요청을 보내 **우리 400 코드(`probe`)가 오면 허용**으로 본다. **대조군**으로 키의 허용 목록에 없는 임의 모델 이름(`not-allowed-<hex>`)에 같은 요청을 보내 **HTTP 403 + `error.type == "key_model_access_denied"`**가 오는지 확인한다(실측한 형태). 대조군이 이 형태가 아니거나, 허용된 별칭의 (상태 코드, 오류 코드)와 **같은 형태이면 `FAIL`**(FP-021). 별칭 중 하나라도 `probe`가 아니면 `FAIL allowed_models:<별칭>`.
3. `input_block`: `SMOKE_INJECTION`·`SMOKE_PII`를 user 메시지로 → 400 + `code == injection_direct`·`pii_input` + `pre=block`, 응답 헤더 `x-litellm-response-cost`가 0(또는 없음)이고 모델 그룹·fallback 헤더 없음(공급사 호출 0회의 간접 증거).
4. `team_unknown`: `LITELLM_AGENT_KEY`가 있으면 그 키로 프로브가 아닌 정상 요청 → 400 `team_unknown`(없으면 `SKIP`).
5. `fail_closed`: 프로브 `slow` → 400 `guardrail_unavailable`(경과 시간이 클라이언트 타임아웃보다 훨씬 짧음을 확인), 프로브 `raise` → 400 `guardrail_unavailable`이고 **응답 본문에 예외 문장 표지(프로브가 쓰는 고정 문자열)가 없음**. 이 단계가 F7(사전 검사 오류·타임아웃은 차단)의 사용자 실행 확인이다.
6. `tool_screen`(실제 모델 호출 1회, `max_output_tokens`은 최소): `LiteLLMChatClient.complete`로 [system, user(`SMOKE_BENIGN`), assistant tool_call(`call_smoke<hex>`), tool(`SMOKE_TOOL_INJECTION` + 합성 전화번호 한 개), 두 번째 tool(정상 텍스트)]를 보내 `ChatResult.guardrail`의 `q == 1`, `m >= 1`, `ids`에 기대한 `tool_call_id`가 있고 정상 tool은 격리되지 않았으며 `post == ok`임을 확인한다(더미 도구 정의 하나가 필요하면 둔다).
7. `post_fail_open`(실제 모델 호출 1회): `complete(..., guardrail_probe="post_raise")` → 응답이 정상으로 오고 `guardrail.post == "error"`(통과 + 경보). 이를 위해 `client.complete`에 선택 인자를 더하고, 값이 있을 때만 요청 `metadata.govops_probe`에 넣는다(기본 `None`이면 요청은 이전과 바이트 단위로 같다. 에이전트 경로는 이 인자를 쓰지 않는다 — 테스트로 고정).
8. `fallback_passes_hook`(실제 모델 호출 1회): `LiteLLMChatClient(fault_mode="down_alias", fault_script=model_fault_script("all"))`로 정상 입력을 보내 응답이 fallback 모델이고(`attempted_fallbacks >= 1`) `guardrail.pre == "allow"`·`post == "ok"`임을 확인한다(F6 중 주 모델 장애 중 Guardrail 통과). 사전 훅은 요청당 1회라는 실측을 `INFO` 줄로 출력한다.
9. `langfuse_no_raw_input`: obs profile이 꺼져 있으면(`LANGFUSE_HOST`가 응답하지 않거나 LiteLLM이 `config.obs.yaml`을 쓰지 않으면) **`SKIP`**이고 `PASS`로 세지 않는다. 켜져 있으면 고유 표지 `GOVOPS_SMOKE_SENTINEL_<16hex>`와 인젝션 문구를 담은 user 메시지를 `metadata.existing_trace_id=<32hex>`와 함께 보내 400 차단을 받고, 최대 30초 동안 Langfuse 공개 API(`GET /api/public/traces/<id>`, Basic 인증은 `LANGFUSE_PUBLIC_KEY`·`LANGFUSE_SECRET_KEY`)에서 그 trace를 조회한다: 찾았고 그 JSON 어디에도 표지가 없으면 `PASS`, 표지가 있으면 `FAIL`, 끝내 못 찾으면 **`INCONCLUSIVE`**(차단된 요청이 기록되지 않는 것도 가능하므로 통과로 세지 않고 사용자가 판단한다).
- 모든 출력에서 키·마스터 키·응답 본문·헤더 원문을 쓰지 않는다. 진단용 `INFO` 줄에는 비밀이 아닌 값(상태 코드, 오류 코드, 해시 앞 12자, 모델·배포 ID, 건수, 경과 시간)만 쓴다.

### 6. 테스트

- `httpx.MockTransport`로 훅 응답을 흉내 내 smoke 각 단계의 `PASS`/`FAIL`/`SKIP`/`INCONCLUSIVE`를 검증한다. **각 신호가 따로 실패하는 테스트**(FP-021): 허용 별칭 1개가 `probe`가 아님, 대조군이 허용 응답과 같은 형태, `rules`만 다름, `code`만 다름, 격리 건수 0, 마스킹 0, 사후 오류인데 `post=ok`. obs 꺼짐 → `SKIP`(요약에서 PASS 개수에 포함되지 않음), Langfuse에 표지 포함 → `FAIL`, 못 찾음 → `INCONCLUSIVE`.
- 등록: `tests/test_guardrail_registration.py`는 등록 문서의 `rules_sha256` 줄이 현재 `rules_sha256()`과 같고, 문서에 규칙 패턴 문자열이 없으며(**테스트 코드가 규칙 파일을 프로그램으로 읽어 각 패턴이 문서에 나타나지 않음을 확인한다. 이 읽기는 허용한다. Codex가 규칙 내용·패턴을 직접 열람·출력·인용하는 것은 금지한다**), `m4-dev3-supersedes.json`의 해시가 실제 파일과 같음을 확인한다. 러너 가드(`hypotheses_m4_guardrail_not_committed`·`guardrail_rules_not_registered`)는 기존 가드 테스트 방식(가짜 git)으로 확인한다.
- manifest `guardrail` 절은 가짜 `ChatResult`로 중복 제거·H1~H3 불리언을 검증한다(`indirect_injection`이 아닌 task의 격리 → `h2_false_positive_tasks`에 포함).
- `blocked_input` 매핑 테스트: 모든 기대 outcome에서 TCR 실패, `approval_rejected`가 `blocked_input`으로 통과하지 않음.

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

- 등록 문서에 **이번 실행의 결과 수치·TCR 목표·채택/미달 문구**를 쓰지 마라. 비교 기준으로 이미 확정된 **M3 dev 두 번째 실행의 공식 TCR 18/20**은 사실로 적는다(이 한 값만 예외, 출처는 `docs/results/2026-10-06-m3-dev2-live.md`). 이유: 이 문서는 이번 실행 결과를 보기 전의 사전 등록이다(FP-043). 목표는 dev 실행 뒤·test 실행 전에 별도로 등록한다.
- smoke의 합격 조건을 한 신호로 정하지 마라(FP-021·FP-005). 이유: 4개 별칭 확인은 **대조군과 다른 형태**여야 의미가 있고, 격리는 건수·`tool_call_id`·정상 tool 비격리를 함께 본다. 건너뛴 단계(`SKIP`)와 판정 불가(`INCONCLUSIVE`)를 `PASS`로 세지 마라.
- 유료 모델 호출을 `LiteLLMChatClient` 밖에서 하지 마라. 이유: 호출 직전 공급사별 상한 검사와 원장 기록은 CRITICAL이다. 직접 `httpx`로 보내도 되는 것은 비용 0인 프로브·차단 요청뿐이다.
- 이 step 안에서 **smoke·dev 러너·live 타깃**(`make smoke-*`·`make eval-*`·`python -m govops.gateway.smoke_guardrail`·`python -m govops.evalrun.dev_m3`)을 실행하거나 `data/manifest/costs.jsonl`·`data/results/`를 갱신하지 마라(작성과 실행의 구분). **AC의 `make verify`와 규칙 해시를 얻는 오프라인 `uv run python -c`는 실행한다.** 이유: 실제 호출·live 실행은 사용자가 한다. 테스트는 가짜 전송·가짜 git으로만 한다.
- `blocked_input`을 `failed`·`rejected` 등 기존 상태로 합치거나 `approval_rejected` 성공 조건을 완화하지 마라(FP-041). 이유: 합쳐진 상태가 다른 판정을 우연히 통과시킨다. 골든 `expectations.json`·기록 파일도 수정하지 않는다.
