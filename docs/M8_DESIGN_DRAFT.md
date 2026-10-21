# M8 설계 초안: JD 정렬 평가·운영 보강

기준: main `18722cc`(`git fetch` 뒤 origin/main과 동일). 이 문서는 untracked이고 사전 등록 커밋에 함께 넣는다(FP-043). 코드·phase 파일·사전 등록 파일·브랜치·커밋·PR은 만들지 않았고 실제 API는 호출하지 않았다. 작업 트리의 `data/manifest/costs.jsonl` 변경은 데모 호출 비용이라 그대로 둔다(FP-063).

**개정 이력(2026-10-16, 조언 세션 지시 12·13)**: 지시 13에서 일치 단위를 결정론 문장으로 바꾸고(분할 규칙 `sent-v1`, 문장 단위 κ·클러스터 CI), DB 읽기를 코드로 읽기 전용으로 만들었으며(`grounding/db.py`, `open_checkpointer` 금지), Langfuse 환경 변수 누락을 `FAIL langfuse env_unset`으로 처리했다. 지시 12: 사용자 결정 10개를 반영했다(8절). 증거는 Postgres 전체 덤프로 보존돼 2026-10-22 시한이 해소됐고, phase는 권장 변형(① 항목 1+5, ② 항목 4, ③ 항목 2+3, 실행 순서 ③ → ① → ②)으로 확정했다. 첫 phase `m8-grading-v3`의 phase 파일(`phases/m8-grading-v3/`)과 사전 등록 초안 3종·라벨링 안내서를 함께 만들었다(모두 untracked).

표기: **실측**은 파일:행으로 확인한 사실, **미실측**은 확인하지 못했거나 사용자 실행이 필요한 사실이다. 열람하지 않은 경로: `.env`, `data/interim/`, `data/eval/`, `data/redteam/`(원문). `data/results/`는 구조 필드 이름과 ID 목록만 읽었고(FP-022: 가공본은 형태 판단의 근거로 쓰지 않았다), 합성 문서 본문은 `data/replay/a_run/T1-01.jsonl` 첫 줄 앞부분만 확인했다.

---

## 0. 외부 계약 실측

성공 여부와 반환 내용을 따로 적는다(FP-050).

### 0-1. 항목 1: Tool-call accuracy

| 사실 | 근거 |
|---|---|
| 명세 필드: `required_tools_any: list[list[str]]`(그룹마다 하나 이상 성공), `forbidden.forbidden_tools: list[str]`. 도구 이름 집합은 `_TOOL_NAMES`(`get_document` 포함)로 검증된다 | `eval/spec.py:20-25,90,126,206-214` |
| 필수 호출 판정: `successful = {ok인 호출 이름}`, `coverage = all(successful & group)`. 금지 호출은 `called & forbidden_tools` → TCR 사유 `forbidden_tool_called` | `eval/tcr.py:198,208-209` |
| `tool_coverage`는 `TcrVerdict` 필드이고 **task×실행마다** `grades.json[task_id].tcr.tool_coverage`로 저장된다(실측: N2-1 `grades.json`의 키 `task_id·run_index·passed·reasons·tool_coverage`). **결과 문서·EVAL 어디에도 집계값이 없다**("진단값"이라는 문장만 있다) | `docs/EVAL.md:43`, `data/results/test/09fbcb2b…/grades.json` |
| 관측의 도구 호출은 `{name, ok}` 두 필드뿐이다. **스키마 위반·오류 코드는 `ok=False`로 합쳐지고 사유가 없다**. `tool_results`는 구조 ID만 남기고 본문을 두지 않는다 | `eval/observation.py:17-20,48-49` |
| 호출별 오류 코드는 trace span 속성 `success`·`error_code`에 있다(도구 이름과 같은 span). 실측: N2-1 trace의 도구 span에 `tool.result={excerpt:'',ids:[…]}`, `run_sql`은 `error_code`·`success`. trace 파일은 실행 디렉터리 `traces/<trace_id>.jsonl`이고 관측의 `trace_id`와 `attempts/<task>-<n>/attempt.json`의 `trace_id`로 연결된다 | `agent/graph.py:566-583`, `data/results/test/09fbcb2b…/traces`, `attempts/T1-02-1/attempt.json` |
| 분모 후보: 공식 N2 3개 실행(`09fbcb2b…`·`b330337d…`·`bf38e57a…`, 각 40 task) = 120 실행. dev N1a(`6b3ec0b1…`)·N1b(`6dba4af2…`) 각 20 | `2026-10-15-m6-redteam-run-live.md` 2절 |
| 성공 여부: 위 파일들은 읽혔다. 반환 내용: `tool_coverage`가 task마다 참/거짓으로 있으나 **필수 그룹이 없는 task(예: 일부 T5)에서 `all([])`가 참**이 된다(`tcr.py:209`). 분모에서 빼지 않으면 FP-069형 공허 통과다 | `tcr.py:209` |

### 0-2. 항목 2·3: Citation precision·Hallucination rate

| 사실 | 근거 |
|---|---|
| **`cited_doc_ids`는 `observed_ids` 중 답변 문자열에 그 ID가 들어 있는 것**이다. 정의상 항상 observed의 부분집합이므로 "인용 ⊆ 관측"은 **항상 참(공허)**이다. 답변이 **관측하지 않은 ID를 말하는 경우는 이 필드로 보이지 않는다** | `agent/graph.py:514-519` |
| 현재 채점 입력: `build_prompt`는 `request`·`deliverable`·`required_facts`·`derived_values`·`criteria`만 담고 **근거 본문 필드가 없다**. groundedness는 근거를 보지 않고 채점된다 | `eval/grading.py:133-154` |
| 사람 채점표 열: `request`·`deliverable`뿐(근거·`required_facts` 없음). 기존 30건의 groundedness는 **근거 본문을 보지 않은 0~3 종합 점수**다. 근거 기반 문장 단위 판정과 **다른 구성**이다 | `eval/human.py:94-101`, `docs/HUMAN_GRADING_GUIDE.md:16,27` |
| `deliverable_text`: 답변이 비어 있지 않으면 답변, 아니면 `effect_bodies[0]`. 승인 발송 task는 답변이 완료 안내문이라 발송 본문이 채점에서 빠진다(사람 표본 30건 중 15건, 이슈 #160) | `eval/accuracy.py:266-269`, `m6-redteam-run-live.md` 7.1절 |
| 관측에는 근거 **본문이 없다**: `tool_results`는 ID만(`result:{documents:[{doc_id}…]}`), trace `tool.result`는 `excerpt:''`+ids. 실측으로 확인했다 | `observation.py:48`, N2-1 관측 첫 행·trace |
| 모델이 본 도구 결과는 messages의 `role:"tool"` 본문이며 **20,000자에서 잘린다**(`…(잘림)`). 증거로 쓰면 이 절단을 재현해야 한다 | `agent/graph.py:70,570-576` |
| 근거 본문이 저장된 곳은 세 곳이다. **S1 기록 재생**: `data/replay/a_run/<task>.jsonl`(12개 dev task, `result.documents[].text`까지 있음. 실측). 테스트 픽스처 형식 참고용이며 M8 채점 근거로 쓰지 않는다. **S2 Postgres checkpoint**: messages의 `role:"tool"` 본문(모델이 본 내용, 20,000자 절단 적용 뒤). M8의 **유일한 근거 출처**다. 메시지 형태(dict/객체)는 G-0 점검으로 확인한다(미실측). **S3 live A 재읽기**: 쓰지 않기로 결정했다(모델이 보지 않은 본문 포함 + 신원 중개 경계). 공식 N2·N1 실행은 `--no-record-golden`이라 S1에 없다 | `data/replay/a_run/`, `Makefile:160-168`, `agent/graph.py:566-576` |
| **증거 보존(해소)**: `make serve`는 시작 때 `startup_prune(apply=True)`로 종료 7일 지난 thread를 삭제한다(`api/retention.py:47-54`, `agent/retention.py:11`). 사용자가 2026-10-16에 Postgres 전체 덤프 `data/interim/m8-evidence/govops-2026-10-16.dump`(pg_dump custom, 열람 금지, 이름·크기만)를 떴으므로 시한은 해소됐다. live DB에서 thread가 지워졌으면 덤프를 임시 DB `govops_m8_evidence`로 복원해 같은 점검·로더를 쓴다(명령은 `hypotheses-m8-grader.md` 2절). 덤프 보관은 ADR-015 예외로 등록하고 삭제 시점은 **M8 결과 PR 머지 직후(사용자 실행)** | `hypotheses-m8-grader.md` 2절, ADR-015 |
| 평가 러너는 `GOVOPS_DATABASE_URL`이 있으면 Postgres saver를 연다(`evalrun/dev_m3.py:1181,1244`). 러너를 `--env-file .env`로 돌렸고 `.env`에 이 변수 값이 있다(`grep -c`=1, 값은 열람하지 않음)는 사용자 보고로 N2·N1이 Postgres에 있을 가능성이 높다. **연결**: 실측으로 T3-05 시도의 비시스템 감사 이벤트 `task_id`가 관측 `trace_id`와 같았고(`attempts/T3-05-1/audit/chain-2026-10-15.jsonl` 3~7행, `attempt.json`의 `trace_id`), thread ID는 `api/tasks.py:72`의 `task_id`다. thread 존재와 메시지 형태는 미실측(G-0 점검). Langfuse: M6의 `langfuse_no_raw_input` 수정으로 거부 요청 본문이 기록되지 않으므로 N2 모델 입력은 0건일 가능성이 있어 **개수만** 점검하고 근거 출처로 쓰지 않는다 | `api/tasks.py:72`, `data/results/test/09fbcb2b…/attempts/T3-05-1/` |
| 채점기: Gemini `gemini-3.8-flash`, 온도 0, 3회, `max_output_tokens` 8192, 호출 직전 `ledger.reserve(purpose="grading")`. 예약 추정은 `2*len(prompt bytes)+4096` 입력 상한. **근거 본문을 프롬프트에 넣으면 예약액이 커진다**. 단가 유효일 2026-12-31(2027-01-01부터 호출 거부) | `eval/grading.py:12-17,174-196`, `costs.py` PURPOSES, EVAL.md 214행 |
| 직전 Gemini 3차(120 호출) 증분 ≈ $0.316(누적 0.5895 → 0.906) = 호출당 ≈ $0.0026, 잔여 $6.38. 근거 본문이 들어가는 v3 프롬프트의 호출당 비용은 **미실측** | `m6-redteam-run-live.md` 1절, `hypotheses-m6-grading-r2.md` 2절 |
| r2 실패 경과(인용 대상): κ correctness 0.652 [0.428,0.806], groundedness 0.282 [0.000,0.564], format 0.223. 표본 30 중 15건이 완료 안내문, 완료 안내문 집단에서 사람 1.20 대 Gemini 2.33 | `m6-redteam-run-live.md` 6·7.1절 |

### 0-3. 항목 4: feature flag 점진 배포

| 사실 | 근거 |
|---|---|
| v2 결정은 **서비스 조립 때 한 번**이다: `semantic = chat`(`GOVOPS_GUARDRAIL_V2 == "on"`일 때, 분류 메서드가 없으면 `guardrail_v2_unavailable`) 아니면 `None`. 그래프에 `graph.agent_semantic = semantic`으로 고정된다. 요청마다 바꾸는 경로가 없다 | `api/wiring.py:59-67`, `agent/graph.py:212,231,344,585-586,1003` |
| 평가 러너는 `build_task_service`를 같은 함수로 부른다. 서비스 조립 함수와 인자는 `semantic`을 이미 주입할 수 있다(`semantic=_FROM_ENV` 기본) | `wiring.py:46-56`, `evalrun/dev_m3.py` |
| 서버 진입점은 `open_task_service()` 하나이고 `app.state.task_service`에 **서비스 하나**를 넣는다. 모든 라우트는 `request.app.state.task_service`로만 얻고 이를 AST·라우트 표 테스트가 강제한다(FP-070) | `api/__main__.py:13,26-27`, `tests/test_api_entrypoint.py` |
| 서비스 안의 잠금(`_locks`·`_approval_locks`·`_conv_locks`)은 **인스턴스별 dict**다. 서비스 두 개를 띄우면 잠금이 공유되지 않는다. 잠금 순서(대화 → task)는 한 서비스 안에서만 보증된다 | `api/tasks.py:56-62,74-78` |
| checkpointer saver·턴 색인(`PostgresTurnIndex`)·outbox는 `open_task_service`에서 한 번 만들어 주입한다. 두 서비스에 같은 saver를 주입하는 것은 구조상 가능하나 thread ID는 대화마다 `uuid4().hex`라 충돌하지 않는다. `startup_cleanup`은 `alist(None)`으로 saver의 **모든** thread를, `outbox.list_open()`으로 열린 outbox 전체를 처리하므로 라우터가 한 번만 부른다(2-3절) | `wiring.py:173-197`, `api/tasks.py:691-750` |
| `build_task_service`는 호출마다 `AuditLog`를 만들고 `service.start`를 **한 번 쓴다**. 두 번 호출하면 `service.start`가 2개 생긴다. 감사 쓰기는 `flock` 아래 `_last_record()`로 다음 seq를 정하므로 인스턴스 둘이 같은 체인 디렉터리를 써도 seq는 충돌하지 않는다(코드 확인: 스레드 잠금은 디렉터리별 공유 `_directory_locks`, `_last_record()`는 캐시 없이 매번 파일을 읽음). 교차 쓰기 합성 테스트는 F-4에서 둔다 | `audit/log.py:321,362,496-527`, `wiring.py:67-93` |
| 감사 레코드에는 `conversation_id`·변형 필드가 없다. 감사 필드·검증기는 동결이다. v2 귀속은 R2 판정이 쓴 감사 `v2=True` 속성으로 이미 보인다 | M7 draft 0-2, `m6-redteam-run-live.md` 4.3절 |
| 기본 프로필은 `writes_team`이다(공식 N2 manifest와 같음) | `wiring.py:170` |
| v2 켬 시 Anthropic 전송 범위가 늘어난다(ADR-026 트레이드오프). 점진 노출은 사용자 일부에게 이 전송을 켜는 결정이다 | `ADR.md` ADR-026 |
| 공식 구성은 v1로 확정됐다(`v2_normal_fp_ge_2`). 점진 배포는 **공식 수치를 바꾸지 않는** 운영 장치로만 쓴다 | `hypotheses-test-targets.md` 1절 |

### 0-4. 항목 5: 모델 버전업 regression

| 사실 | 근거 |
|---|---|
| 클라이언트는 `body["model"]="agent-primary"` 고정이고 모델 ID 상수 `_MODEL="gpt-6-luna"`로 입력 상한·원장 예약(`("agent_primary", _MODEL, "openai")`)을 만든다. 모델 선택 경로가 코드에 없다. **후보 모델은 LiteLLM 설정의 `agent-primary` 별칭이 가리키는 배포를 바꾸는 방법뿐**이다 | `gateway/client.py:44,230,244-257` |
| 설정 교체 통로는 있다: compose가 `${LITELLM_CONFIG_FILE:-./litellm/config.yaml}`을 마운트하고 `config.obs.yaml`이 같은 방식으로 쓰인다(`obs-up`은 `--force-recreate`) | `infra/docker-compose.yml:38`, `Makefile:129-139` |
| 응답 모델은 헤더 `x-litellm-model-id`(배포 ID)로 정하고 등록 배포는 `openai-gpt-6-luna`·`-only`·`anthropic-claude-sonnet-5-5`뿐이다. **미등록 ID는 최대 단가로 보수 정산 + 경보**, `unmapped_models`는 결과 문서에 표시한다 | `costs.py:145-155`, ADR-019 5항, EVAL.md 64행, FP-020 |
| 사전 점검 `litellm_config_stale`: `/model/info`가 돌려주는 배포 ID 집합이 `required_deployments()`(등록 배포 + `-down`)를 **포함**해야 한다. 후보 설정은 기존 배포 ID를 모두 유지하고 `agent-primary` 별칭의 대상만 바꿔야 점검을 통과한다 | `evalrun/preflight.py:34-45,115-126` |
| 모델 이름 집합은 `config.yaml`·`config.obs.yaml`과 에이전트 키 허용 모델 목록의 일치를 테스트가 고정한다. 새 설정 파일은 같은 `model_name` 집합이어야 하며 `config.yaml`은 digest 동결이다 | ADR-019 10항, `test_m7_freeze.py` `infra/litellm/config.yaml` |
| 실행 통로: `make eval-run-dev-m6 CONFIG=v1 RUN=<id>`는 `--no-record-golden`으로 dev 20을 live 실행한다. `RUN_CONFIGS=("v1","v1+v2")`, `guard_switch`가 `GOVOPS_GUARDRAIL_V2`와 CONFIG의 일치를 요구한다 | `Makefile:160-163`, `evalrun/run_guards.py:32,150-155` |
| 골든 14개는 dev 20의 부분집합이다. `golden.digest_for(spec, obs)`가 `tool_order`·`effect_count`·`status`·`refused`·`forbidden_tool_called`(+쓰기 task의 `effects`·`approval_decisions`)를 뽑는다. 기준 기대값은 `data/replay/golden/expectations.json`(동결). **후보 관측에서 `digest_for`를 다시 계산해 기대값과 비교**하는 오프라인 경로가 가능하다 | `golden.py:35-50,154-195` |
| 기준선: 공식 dev N1a(v1) TCR 18/20, N2 TCR 0.858·pass^3 0.85·p95 14.87 s. 지연 정의: 승인 대기 제외 루트 span `duration_ms` 합, nearest-rank | `m6-redteam-run-live.md` 1·3·5절, `hypotheses-test-targets.md` 2절 |
| 판정 형식 선례: `verdict()`가 `{official_config, reasons[], criteria{1..5:{met,…}}, normal_control}` JSON을 내고 sha256을 등록 문서에 옮긴다 | `eval/run_summary.py:201-257` |
| 러너 가드: 사전 등록 커밋·깨끗한 작업 트리(원장만 예외)·결과 디렉터리가 untracked로 남는 문제(FP-067). `--no-record-golden`이면 `data/replay`를 덮어쓰지 않는다 | `run_guards.py:163-169`, `dev_m3.py:1297,1331`, FP-067 |

---

## 1. 동결 경계

### 1-1. 바꾸지 않는 것

M7 draft 1절의 목록이 M8에서도 그대로다(`scripts/freeze_digest.py` `FREEZE_PATHS`, `tests/test_m7_freeze.py` `EXPECTED`). 에이전트(그래프·프롬프트·도구 설명·Guardrail 규칙·v2 설정), `gateway/`, `costs.py`, `eval/`, `evalrun/`, `replay/`, `mocks/`, `data/{contract,policy,replay,seed,eval,redteam}`, `infra/litellm/config.yaml`, 감사 쓰기·검증기, 기존 `hypotheses-*.md`와 `docs/results/m6-redteam-run/`은 읽기 전용이다. **공식 구성은 v1이고 M8은 이를 바꾸지 않는다.**

### 1-2. M8이 쓰는 곳(추가 전용)

| 구분 | 경로 | 비고 |
|---|---|---|
| 신규 패키지 | `src/govops/metrics/`(항목 1), `src/govops/grounding/`(항목 2·3), `src/govops/regress/`(항목 5), `src/govops/rollout/`(항목 4 순수 로직) | `eval/`·`evalrun/`이 **디렉터리 digest**라서 그 안에 새 파일을 두면 digest가 바뀐다. 그래서 새 패키지로 둔다. `AGENTS.md` 계층 표 행 추가와 `tests/test_architecture.py` 규칙 행 추가는 승인됐다(8절 9번). 패키지별 행은 그 패키지를 만드는 phase의 첫 step에서 더한다 |
| 신규 파일 | `src/govops/api/variant.py`·`routed.py`·`rollout_wiring.py`·`rollout_exercise.py`(항목 4) | `api/`는 동결 목록 밖. 기존 함수 본문 불변이 원칙이고 `api/__main__.py`의 진입 분기만 바뀐다(8절 결정 9, 승인됨). 예외: `api/retention.py`의 `run_prune`·`startup_prune`·CLI에 변형 행 삭제를 더한다(지시 46, 2-3절) |
| 신규 설정 | `infra/litellm/config.candidate.yaml`(항목 5) | `config.yaml`은 동결이므로 복사본이 아니라 별도 파일 |
| 문서·스크립트 | `docs/results/hypotheses-m8-*.md`, `docs/results/…-m8-*.md`, `Makefile` 타깃 추가, `scripts/` 신규 | 기존 `hypotheses-*.md`는 수정 금지 |
| 테스트 | `tests/test_m8_*.py` | 소비자 테스트 갱신은 2절 |

### 1-3. digest 상수 갱신 절차

신규 파일·신규 패키지만 쓰면 `EXPECTED`는 **바뀌지 않아야 한다**(기본 기대). 동결 경로를 바꿔야 하면 "동결 위반 후보"로 따로 올리고 아래를 전부 지킨다.

1. 변경 사유·대상 파일·이전 digest·새 digest를 새 사전 등록 파일(`hypotheses-m8-*.md`)에 적고 **변경 커밋보다 먼저** 커밋한다.
2. 같은 PR에서 `EXPECTED`의 해당 키만 갱신한다(다른 키 불변을 테스트가 확인).
3. 공식 구성 불변 증거를 붙인다: `test_golden_m3`·`test_regolden`·`test_golden_trace` 무수정 통과, 사용자 실행 `service.start` 해시 대조(`policy_sha256`·`rules_sha256`·`hook_*_digest` 공식 test 때와 동일).
4. M6 결과 문서는 수정하지 않는다. M8 결과 문서에 "digest 변경 이력" 표를 둔다.
5. M8 사전 등록 파일은 등록 직후 동결 목록에 넣는다. `scripts/freeze_digest.py`는 건드리지 않고 `tests/test_m8_freeze.py`가 `FREEZE_PATHS`에 M8 목록을 더해 대조한다.

**동결 위반 후보(설계에 넣지 않음)**

| 후보 | 처리 |
|---|---|
| `ToolCallRecord`에 오류 코드·스키마 위반 필드 추가 | 하지 않는다. trace span의 `error_code`로 보충(항목 1) |
| 평가 러너가 근거 본문을 저장하도록 변경 | 하지 않는다. S1·S2·S3로 얻는다 |
| `agent/graph.py:518` `cited_doc_ids` 정의 변경(답변이 말한 미관측 ID 포착) | 하지 않는다. 답변 문자열에서 ID를 별도 추출해 측정하고 한계로 보고. 에이전트 불변 |
| `costs.py` 가격표에 후보 모델 단가 등록, `gateway/client.py` `_MODEL`/별칭 변경 | 하지 않는다. 후보는 이미 등록된 모델(`anthropic-claude-sonnet-5-5`)을 `agent-primary` 별칭에 꽂는 방식을 기본으로 하고, 새 OpenAI 모델을 쓰려면 이 후보를 승인해야 한다(8절 결정 5는 `claude-sonnet-5-5`로 확정했으므로 해당 없음) |
| `run_role=reference`로 골든·결과 경로를 분리(FP-067 미구현) | 하지 않는다. `--no-record-golden`과 실행 사이 결과 디렉터리 이동으로 푼다 |
| 감사 레코드에 변형·`conversation_id` 필드 | 하지 않는다. 노출 기록은 별도 JSONL과 trace 속성 |

### 1-4. 공식 M6 수치와 M8 수치의 분리

- 공식 수치는 `m6-redteam-run-live.md`와 `hypotheses-test-targets.md`가 정본이고 M8은 다시 쓰지 않는다. 재집계는 입력 파일(`observations.jsonl`·`grades.json`·`manifest.json`)의 sha256을 출력에 기록하고 **읽기 전용**으로 읽는다.
- M8 산출물은 `data/results/m8/<항목>/…`(gitignore 밖이면 결과 PR에서 옮김)와 결과 문서 `docs/results/2026-10-xx-m8-*-live.md`에 두고, 모든 표 머리에 `M8 추가 측정 — 공식 M6 수치 아님`과 `scope: "m8"` 필드를 넣는다.
- README는 "공식(M6, 등록 목표 있음)"과 "M8 측정(보강, 등록 목표 있음/없음 구분)"을 다른 표로 쓴다. 한 표에 섞지 않는다.
- 새 지표가 기존 지표 이름과 겹치지 않게 한다: `tool_coverage`(기존 진단값)와 `required_tool_call_rate`(M8), 기존 groundedness와 `unsupported_sentence_rate`(M8).

---

## 2. 항목별 설계·테스트·소비자 테스트(FP-071)

### 2-1. 항목 1: Tool-call accuracy (`govops/metrics/toolcall.py`)

**정의(등록 대상)**

| 지표 | 분자/분모 | 비고 |
|---|---|---|
| 필수 도구 호출률 | 분모 = `required_tools_any`가 **비어 있지 않은** task×실행. 분자 = 모든 그룹에서 성공 호출이 하나 이상 | 빈 그룹 task는 분모에서 빼고 건수를 따로 보고(`all([])` 공허 참 방지, FP-069). 분모 0이면 실패 코드 |
| 금지 호출 건수 | 분자 = 실행 안의 금지 도구 **호출 횟수**(task 수와 횟수를 둘 다). 분모 = 전체 실행 | 0이 좋은 지표이므로 **짝 지표**로 필수 호출률과 TCR을 함께 보고(FP-040) |
| 호출 성공률 | 분자 = `ok` 호출 수, 분모 = 전체 도구 호출 수(읽기·쓰기 구분 보고) | 스키마 위반/오류 코드 분해는 trace의 `error_code`를 task별로 연결할 수 있을 때만(연결 실패는 `unlinked`로 건수 보고, 조용히 빼지 않음) |

- 위치: 입력은 `observations.jsonl`·`grades.json`(참고 대조용)·명세(`load_specs`)·선택적으로 `traces/`. **판정은 `grade_tcr`를 거치지 않고 별도 순수 함수**로 하되, 기존 `tool_coverage`와의 일치(`required_tool_call_rate` 분자 = coverage 참이고 그룹 비어 있지 않음)를 테스트로 검증한다.
- 대상: 공식 N2 120 실행(공식 수치와 분리해 보고), dev N1a·N1b(참고). 비용 0.
- 목표: **없음, 측정·보고만**. 공식 TCR과 합치지 않는다. 합격선을 두는 항목은 항목 5의 상대 한도뿐이다.
- 불확실: `ok`가 "스키마 유효·오류 코드 없음"을 뜻하는지는 도구 계층이 스키마 위반을 예외로 던지는지에 달려 있다. 결정 10에 따라 지표 이름은 **`call_ok_rate`(호출 성공률)**로 확정하고, T-0에서 `tools/registry.py`·`tools/contract.py` 계약 테스트로 `ok=False`의 범위를 확인해 등록 문서에 적는다.

**테스트(합성)**: 필수 그룹 2개 중 1개만 성공 → 미충족, 금지 도구 2회 호출 → 건수 2·task 1, 빈 그룹 task → 분모 제외 + 건수 표시, `ok=False`만 있는 그룹 → 미충족, 입력 파일 변조 시 sha256 불일치 표시, 분모 0 → 실패 코드, 기존 `tool_coverage`와의 일치(합성 100건 무작위 시드 고정, 건수 명시).
**소비자(FP-071)**: 신규 패키지라 기존 정확 일치 단언이 없다. 확인할 것: `tests/test_architecture.py`의 패키지 목록·`FORBIDDEN_IMPORTS`, `tests/test_docs_consistency.py`(AGENTS.md 표), `tests/test_makefile.py`(타깃 추가 시 verify 레시피 정확 일치는 불변), `tests/test_env_defaults.py`(새 env 없음). 시뮬레이션은 `git clone --local` 복사본(FP-072)에서 전체 pytest.

### 2-2. 항목 2·3: Citation precision / Hallucination rate (`govops/grounding/`)

**측정 단위와 정의(등록 대상)**

- 채점 입력(deliverable): 발송 task는 `effect_bodies[0]`, 읽기 task는 `answer`(완료 안내문이면 발송 본문). 이슈 #160을 **채점 입력 쪽에서** 해결하고 에이전트는 불변이다. `deliverable_text`(동결)를 쓰지 않고 새 선택 함수를 둔다.
- **문장(단위)**: 채점 대상 문서를 결정론 분할 규칙 `sent-v1`(줄바꿈·글머리표 제거·`. ? !` 뒤 공백·한국어 종결 어미 뒤 공백, 코드 `grounding/sentences.py`, 소스 sha256 등록)로 나눈 번호 붙은 문장(`s1`…). 채점기가 주장을 스스로 나누지 않는다. 문장 라벨 4종: `supported`·`unsupported`·`contradicted`·`not_claim`(인사·형식·부재 진술, 판단 안내 목록 등록). 양성 = `unsupported`·`contradicted`.
- **인용(citation)**: 답변/발송 본문에 나타난 문서·티켓 ID(정규식 `syn-[a-z]+-\d+`·티켓 키·`INC-YYYY-NNN`). **`cited_doc_ids`는 쓰지 않는다**(0-2: 공허).
- **Citation precision**(인용 단위) = 인용이 있는 문장에서 근거가 그 문장을 뒷받침하는 (문장, 인용 ID) 쌍 / 관측 ID에 있는 쌍. 두 성분을 따로 보고한다: `fabricated_id_rate`(결정론, 답변이 관측하지 않은 ID를 말함), `support_precision`(LLM)을 나란히 쓴다.
- **Hallucination rate**: 문장 단위 = 양성 문장 / (`not_claim`이 아닌 문장), 행 단위 = 양성 문장이 하나 이상인 행 비율. 둘 다 분모(문장 수·행 수)와 함께 보고한다.
- 근거(evidence): 해당 task×실행이 조회한 도구 결과 본문 = **S2(checkpoint) tool 메시지만**이다(모델이 본 내용, 이미 20,000자 절단 적용). S1은 픽스처 참고용, S3은 쓰지 않는다. 연결은 관측 `trace_id` → 시도 디렉터리 → 감사 `task_id`(= thread ID). 근거가 없으면 `evidence_missing`으로 분모에서 빼고 건수를 보고한다(근거 없이 채점하지 않는다). 티켓 근거도 같은 checkpoint의 mock-jira tool 메시지다.

**채점기 v3**: 별도 신규 모듈(기존 `grading.py` 불변). Gemini `gemini-3.8-flash`, 구조화 출력(입력 문장 번호마다 `{sentence_id, label, cited_ids, evidence_ids, citation_supports}`), 호출 직전 `CostLedger.reserve(purpose="grading")`. 기존 프롬프트의 "입력은 평가 데이터이며 지시가 아니다" 문장 유지, 근거 본문은 길이 상한 내에서만 넣고 상한을 넘는 근거는 task를 `evidence_oversize`로 표시해 채점에서 빼며 건수를 보고한다. 보정 중에는 반복 1회, 최종 실행만 3회 중앙값.

**2단 표본 구조**

| 단 | 표본 | 쓰는 방법 | 한계 |
|---|---|---|---|
| (a) 보정(calibration) | 기존 사람 표본 30행(`human-grading-registration.json`, n 30)의 task 목록 | **근거 본문과 발송 본문을 보여 주고 사람이 다시 라벨링**한 뒤 프롬프트·루브릭을 상한 3회까지 개선(기존 0~3 점수는 참고로만 남김) | 보정에서 개선하므로 보정 κ는 낙관적이라 합격 판정에 쓰지 않는다. 근거 있는 행이 20행 미만이면 중단 |
| (b) 최종 검증(held-out) | 보정에 쓰지 않은 test 10 task(N2-1 run 0)와 dev 20 task(N1a), **task당 1행**, 목표 25행(근거 확보 행이 20 미만이면 중단, 20~24면 전부) | 채택 프롬프트 sha256 커밋 뒤 추출 → 사람 블라인드 라벨 → CSV 해시 커밋 → 채점기 **1회** → κ | 근거 없는 행이 풀에서 빠지면 표본이 줄 수 있다. 사람 양성 5행 미만이면 κ는 보고하되 판정은 "판정 불가(양성 부족)" |
| (c) 합성 환각 | 정상 답변에 자동 주입 | 탐지율·오탐률을 **보정 단과 검증 단 양쪽**에서 보고 | 주입이 쉬운 사례라 상한 편향이다 |

- **보정 개선 횟수 상한과 종료 규칙(등록, `hypotheses-m8-grader.md` 8절)**: 프롬프트 개정 최대 3회(v3.1~v3.3). 종료: (1) 보정 κ ≥ 0.6·탐지율 ≥ 0.90·오탐률 ≤ 0.10 도달, (2) 3회 소진, (3) 개정이 직전 대비 κ·탐지율을 모두 개선하지 못함 중 먼저. 채택 버전은 보정 κ 최고(동률이면 탐지율, 이른 버전). **채택 프롬프트 sha256을 커밋한 뒤에야** held-out을 추출한다. 이후 프롬프트·루브릭·합격선은 바꾸지 않는다.
- **κ**: 주 지표는 **문장 단위 이진 Cohen κ**(양성 = unsupported|contradicted, 음성 = supported|not_claim, held-out 모든 문장)이고 CI는 **행 단위 클러스터 부트스트랩**(시드 20261005, 10,000회)이다. 행 단위 κ는 보조 지표. 4×4 혼동 행렬과 채점기의 양성 정밀도·재현율(사람 기준)을 함께 보고한다. **판정 불가**: 사람 양성 문장 10개 미만 또는 양성 문장이 있는 행 5개 미만이면 "판정 불가(양성 부족)"(수치는 그대로 보고). 인용 지지는 인용 단위 별도 라벨(`지지`/`비지지`).
- **합성 주입 생성(결정론, 시드 등록)**: 정상 행(사람 양성 문장 없음)에 (i) 근거에 **없는** 사실 문장을 **새 번호**로 추가(값 문자열이 근거 전체에 부분 문자열로도 없음을 검사), (ii) 인용 ID를 관측하지 않은 ID로 교체, (iii) 지지 인용을 지지하지 않는 관측 ID로 교체. **탐지 = 그 문장 번호가 양성으로 판정됨**(부분 문자열 매칭 폐기, 인용 교체형은 해당 문장의 인용이 비지지). **오탐률 = 주입하지 않은 정상 문장(사람 `supported`) 중 양성 판정 비율(문장 단위)**, 행 단위는 보조.
- **합격선(확정, 지시 12 결정 4)**: held-out **문장 단위 κ** ≥ 0.6(점추정, 클러스터 CI 하한 병기), 합성 탐지율 ≥ 0.90, 합성 오탐률 ≤ 0.10, 사람 양성 문장 10개 미만 또는 양성 행 5개 미만이면 "판정 불가(양성 부족)". **미달이어도 수치·원인 진단을 그대로 보고하고 기준을 바꾸지 않는다.** 미달 시 README는 해당 지표를 수치로 주장하지 않는다(7절). Gemini M8 상한 $2(보정 0.8·검증 0.6·측정 0.6), 2026-11-30까지 완료.

**테스트(합성)**: 가짜 Gemini 호출(고정 JSON)로 문장 분할 규칙·번호 집합 검증·인용 추출(ID 정규식 경계, 20,000자 절단 재현), 근거 출처 단일화(S2만, S1·S3 미사용), 근거 없음 → `evidence_missing`(분모에서 제외 + 건수), 주입 생성이 구성에 의해 근거 밖임을 보장하는지, 문장 단위 κ와 클러스터 부트스트랩, DB 읽기 전용(setup 미호출·읽기 전용 옵션), 표본 추출 시드 결정론과 보정/검증 표본 **교집합 공집합**, CSV 해시 등록 후 변경 시 무효, 비용 예약 상한 초과 시 호출 거부, 토큰·키가 로그에 없음.
**소비자(FP-071)**: `tests/test_architecture.py`(신규 패키지 행: `grounding`은 Gemini 직접 호출이므로 `eval`처럼 `gateway`·`broker` import 금지), `tests/test_eval_grading.py`·`test_eval_human.py`는 불변(기존 모듈 미수정), `tests/test_costs*.py`의 `PURPOSES` 집합 단언(새 purpose를 만들지 않고 기존 `grading` 재사용), `tests/test_repo_rules.py`(문서 규칙), `tests/test_docs_consistency.py`.

### 2-3. 항목 4: feature flag 점진 배포 (`govops/rollout/` 순수 로직 + `api/routed.py`)

**개정(2026-10-21, 지시 46)**: main `94f6cd4`에서 코드와 다시 대조해 db-init 범위, 목록 메서드, 롤백 지표 수집 경로, 서비스 생성 시점, 변형 행 기록 시점, 라우터 조건, 감사 두 인스턴스를 정정했다. 이전 문장 "pct=0이면 v2 서비스를 지연 생성하지 않아 `service.start`가 1개"는 자기모순이라 지웠다.

**구성**

- **버킷**: `digest = sha256(SALT ‖ user_label)`, `bucket = int(digest[:8], 16) % 10000`. `SALT = "govops-m8-rollout-v1"`은 저장소의 비밀 아닌 상수다(바꾸면 전원 재배정이라 변경 금지로 등록). 기록에는 사용자 라벨 대신 `user_sha256 = digest`만 남긴다.
- **상태 파일**: `<rollout_dir>/state.json` `{pct_bp, kill, rolled_back_at, reason}`(원자적 교체, 0600, 실행 산출물이라 커밋 안 함). `rollout_dir` 기본 `data/interim/rollout`(`GOVOPS_ROLLOUT_DIR`, `env_or`). 파일 없음 = `pct_bp=0, kill=false`. 파일 손상·형식 오류 = `kill=true, reason="state_invalid"`(fail-safe, 전원 v1).
- **켜는 스위치**: `GOVOPS_V2_ROLLOUT`(`on|off`, 기본 `off`, `env_or`, FP-030)만 이 장치를 켠다. `GOVOPS_GUARDRAIL_V2=on`과 동시에 켜면 감사 쓰기 전에 `StartupError("rollout_conflict")`로 시작을 거부한다.
- **서비스 생성**: 롤아웃이 켜지면 시작 때 v1 서비스(`build_task_service(semantic=None)`)와 v2 서비스(`build_task_service(semantic=RecordingClassifier(chat))`)를 **둘 다** 만든다(`service.start` 2개). 같은 디렉터리에 두 번 조립하면 `service.start`가 2개 생기는 것은 기존 `tests/test_service_start_event.py::test_start_hashes_and_shared_genesis`가 이미 예상한다(65~71행: 순차 두 번 조립, 68~69행이 `service.start` 두 개). 두 서비스는 saver·턴 색인·outbox·도구·chat을 공유하고 감사 로그는 같은 디렉터리의 인스턴스 둘이다. **롤아웃 off(기본)는 지금과 같은 `open_task_service` 경로이고 서비스는 1개다**(테스트로 보증). pct=0이면 v2 서비스가 있어도 배정이 0이라 분류 호출(Anthropic) 0건이다(가짜 chat의 `classify` 호출 수 0으로 테스트).
- **조립 위치**: `api/rollout_wiring.py`의 `open_rollout_service()`가 조립하고 `api/__main__.py`의 lifespan이 `GOVOPS_V2_ROLLOUT`에 따라 `open_rollout_service`와 `open_task_service` 중 하나를 고른다. 두 이름은 lifespan 실행 시점에 모듈 전역에서 찾으므로 `tests/test_api_entrypoint.py`·`tests/test_api_wiring.py`의 `monkeypatch.setattr(entry, "open_task_service", …)`가 그대로 통한다. `wiring.py`의 기존 함수 본문은 바꾸지 않는다(1-2절 원칙). 그래서 `open_rollout_service`는 `open_task_service`의 사전 조립(fault mode 거부·비밀 등록·trace 설정·팀 키·`KODOCS_MCP_URL`·계약 확인·REST 클라이언트·saver·프로필·outbox·턴 색인)을 같은 순서로 다시 쓴다. 두 경로의 어긋남은 같은 실패 입력에서 같은 `StartupError` 코드가 나오는지 확인하는 동등성 테스트로 막는다.
- **변형 저장소**: Postgres 신규 테이블 `govops_conversation_variant(conversation_id PK, variant, bucket, pct_bp, assigned_at)`(`api/variant.py`, `PostgresTurnIndex._run` 패턴). **`make db-init`은 DB만 만들고(Makefile 83~84행) 테이블은 저장소 코드가 처음 쓸 때 `CREATE TABLE IF NOT EXISTS`로 만든다**. 그래서 db-init은 바꾸지 않는다. 삭제 경로는 테이블이 없으면(`to_regclass`가 NULL) 만들지 않고 0을 돌려준다(롤아웃을 켠 적 없는 DB에서 `make checkpoint-prune`이 테이블을 만들지 않게). `make test-m5-pg`(사용자 실행)는 `tests/test_m5_pg.py`에 변형 테이블 케이스를 더하는 것으로 확장한다.
- **변형 행 기록 시점**: `conversation_id`는 `TaskService.start()` 안에서 만들어지고 같은 호출 안에서 턴이 시작된다(`api/tasks.py:201-225`). 그래서 라우터는 배정한 변형의 서비스로 `start_conversation`·`start`를 부른 **뒤** 변형 행을 쓰고 노출 기록을 덧붙인다. 행이 없는 대화(행을 쓰기 전에 서버가 죽은 경우 등)는 v1로 보내고 `variant_missing`을 대화당 한 번 노출 기록에 남기며 CLI `status`가 건수를 출력한다. 한계(등록): 행 쓰기 전에 죽은 v2 대화는 재시작 뒤 v1 서비스가 처리한다.
- **변형 행 삭제**: thread를 실제로 지우는 지점에서만 함께 지운다. `retention.run_prune`이 `turns.delete_conversations(result.deleted_ids)`를 부르는 곳에서 `variants.delete(result.deleted_ids)`를 부른다. 이 경로를 지나는 것은 `startup_prune`(서버 시작)과 `checkpoint-prune` CLI(`python -m govops.api.retention`)다. `delete_conversation`은 소프트 삭제(`mark_deleted`)라 thread가 남으므로 변형 행도 남긴다. 이유: `is_deleted`는 공유 색인이라 어느 서비스로 가도 404이고, 행을 먼저 지우면 v1 서비스가 v2 thread를 맡게 되고 `variant_missing`이 거짓으로 늘어난다.
- **변형 고정(sticky)**: 이후 모든 턴·승인·조회는 저장된 변형의 서비스로 간다. 킬 스위치와 비율 변경은 새 대화에만 영향이다. 한계(등록): 킬 스위치 뒤에도 진행 중인 v2 대화는 끝날 때까지 v2로 남는다(즉시 전환은 승인 대기 `interrupt` thread에 새 입력을 넣는 위험이 있어 하지 않는다).

**라우터(`RoutedTaskService`)**

`app.state.task_service`에는 라우터 하나만 들어가므로 FP-070 라우트 표는 바뀌지 않는다. 라우트가 쓰는 공개 메서드는 `start`·`start_conversation`·`add_turn`·`delete_conversation`·`conversations`·`conversation_view`·`progress`·`audit_view`·`audit_verify`·`approvals`·`get`·`approve`·`aclose`와 속성 `audit`다(`api/app.py`, `__main__.py`가 `app.state.audit`로 읽음).

`tasks.py` 전체에서 공유 자원을 순회하는 메서드(실측):

| 메서드 | 순회하는 공유 자원 | 라우터 처리 |
|---|---|---|
| `conversations` | `turns.list_for_owner`(소유자의 모든 대화) | 서비스마다 **걸러진 턴 색인**을 주고 두 결과를 합친 뒤 `(updated_at 내림차순, conversation_id 오름차순)`으로 다시 정렬하고 `limit`을 적용 |
| `approvals` | `turns.awaiting()`(모든 대기 턴) | 걸러진 턴 색인, 합친 뒤 `(expires_at, task_id)`로 다시 정렬 |
| `startup_cleanup` | `graph.checkpointer.alist(None)`(모든 thread) + `effects.outbox.list_open()`(열린 outbox 전체) | v1 서비스에서 **한 번만** 호출 |
| `audit_verify` | 감사 디렉터리 전체(읽기, 인스턴스별 캐시) | v1 서비스에 위임(같은 디렉터리) |
| `audit_view` | `turns.turn_ids_of(대화 하나)` | 대화의 변형 서비스에 위임 |
| `get`·`approve` | `_resolve` → `turns.conversation_of(턴 하나)` | 라우터가 `turns.conversation_of(task_id) or task_id`로 대화를 찾아 그 변형 서비스에 위임 |
| `start`·`start_conversation` | 없음(새 대화) | 배정 → 위임 → 변형 행·노출 기록 |
| `add_turn`·`delete_conversation`·`progress`·`conversation_view` | 대화 하나 | 대화의 변형 서비스에 위임 |
| `aclose` | 자기 백그라운드 작업 | 두 서비스 모두 |
| `anchor` | 감사 고정점 | 서버 경로에서 쓰지 않는다(호출처 `audit/anchor.py`·`audit/smoke.py`는 `AuditLog`를 직접 씀). 라우터에 두지 않는다 |

- **걸러진 턴 색인**: `awaiting()`·`list_for_owner()`만 자기 변형의 대화로 거르고 나머지(쓰기 포함)는 그대로 전달한다. 행이 없는 대화는 v1로 본다. 거르지 않으면 같은 대화가 두 번 나오고, 다른 변형의 대화를 자기 잠금으로 `_expire`하게 된다(잠금 dict는 인스턴스마다 따로, `tasks.py:56-62`). `list_for_owner(owner, limit)`는 공유 색인이 먼저 전체에서 상위 `limit`개를 고르므로 변형별로 거른 결과를 합치면 정확히 원래의 상위 `limit`개다.
- **startup_cleanup 한 번**: v1 그래프로 v2 thread를 갱신하게 된다(`_update`의 `aupdate_state(as_node="model"|"check")`). 두 그래프는 같은 `build_graph`·같은 `AgentState`·같은 프로필로 만들어지고 `semantic`은 노드 본문만 바꾸므로 상태 형태와 노드 이름이 같다(`agent/graph.py:955-998`). v2 그래프로 만든 thread를 v1 서비스의 `startup_cleanup`이 갱신한 뒤 v2 서비스가 읽는 합성 테스트로 묶는다. `startup_prune`도 한 번만 부르고 변형 저장소를 함께 넘긴다.

**노출 기록과 롤백 지표 수집**

- **노출 기록**: `<rollout_dir>/exposures.jsonl`(append-only). 행 `event`는 `assigned`·`variant_missing`·`rollback` 셋이고 키는 `ts`·`conversation_id`·`user_sha256`·`bucket`·`variant`·`pct_bp`·`kill`·`reason`만 쓴다. 본문·토큰·사용자 라벨은 없다. 감사 체인은 건드리지 않는다.
- **분류 호출 기록(`RecordingClassifier`)**: 턴은 서비스 내부 백그라운드 작업(`_launch` → `_run`, `tasks.py:85-91,227-271`)에서 끝나 라우터가 끝나는 시점을 모른다. 분류 지연은 trace span(`agent/graph.py:299-317`, `guardrail.v2_ms`)에만 있다. 그래서 v2 서비스의 `semantic` 자리에 `classify`만 그대로 전달하는 계측 래퍼를 넣는다. `classify(request=, task_id=, user_id=)`(`gateway/client.py:385-525`)는 `ClassifyResult(label, status, …)`를 돌려주고 `status`는 `ok|timeout|rate_limited|error|parse|v1_blocked`다. timeout은 `APITimeoutError`·`GatewayError(timeout=True)`(설정 `timeout_s`), parse는 `semantic.parse_label`이 `None`일 때, 그 밖의 예외는 `error`로 정해지고 예산·원장 예외(`BudgetExceededError`·`LedgerMissingError`)만 밖으로 던진다. 래퍼는 호출마다 `(turn_id=task_id, status, started, ended)`를 `<rollout_dir>/classify.jsonl`에 남긴다(예외는 `status="raised"`로 남기고 그대로 다시 던짐). 결과·인자·예외를 바꾸지 않고 v2 설정(`classifier_v2.json`)·`GOVOPS_GUARDRAIL_V2` 경로를 건드리지 않는다.
- **세 지표**(평가 단위는 대화, 창은 최근 배정된 대화 중 상태가 `running`·`awaiting_approval`이 아닌 것 K=20개):
  1. 분류 비정상 비율 = 창의 v2 대화에 속한 턴의 분류 호출 중 `status ∈ {timeout, rate_limited, error, parse, raised}`의 비율. `v1_blocked`(게이트웨이 v1 훅이 분류 요청을 막음)는 분모에만 들고 따로 센다. 턴→대화 연결은 평가 시점에 `turns.conversation_of(turn_id)`로 한다.
  2. 차단 비율 차 = (창의 v2 대화 중 현재 상태가 `blocked_input`인 비율) − (최근 v1 대화 창 K=20의 같은 비율). 상태는 평가 시점에 각 변형 서비스의 그래프에서 `get_task`로 읽는다. 한계(등록): 대화의 마지막 턴 상태만 보이므로 앞 턴의 차단은 세지 않는다. v1 차단도 v2 쪽에 섞인다(v2 서비스에도 v1 훅이 돈다).
  3. 분류 p95 = 창의 v2 대화에 속한 턴마다 분류 호출 구간 `[started, ended]`의 합집합 길이(ms), 그 값들의 nearest-rank p95. M6 기준값 8000 ms를 쓰지만 M6 값은 trace의 `semantic` span 합(`eval/redteam_judge.py::v2_span_ms_from_trace`)이라 측정 지점이 다르다(등록 한계).
- **판정**: 새 대화를 배정할 때마다 먼저 평가한다. v2 창이 20개 미만이면 롤백도 확대도 하지 않는다(`insufficient_v2_sample`). v1 대조 창이 20개 미만이면 기준 2만 보류한다(`insufficient_v1_control`). 창에 분류 호출이 0건이면 롤백한다(`rollback_no_classify_calls`, FP-069: 빈 집합으로 통과하지 않음). 임계 초과(같으면 미초과): 비율 > 0.05 → `rollback_classify_abnormal`, 차 > 0.10 → `rollback_block_diff`, p95 > 8000 ms → `rollback_classify_p95`. 롤백은 `state.json`을 `pct_bp=0, kill=true, reason=<코드>`로 쓰고 노출 기록에 `rollback` 행을 남긴다. 자동 확대는 없다(비율은 CLI `set`만 바꾼다).
- **v2 설정 불변**: v2 서비스는 `classifier_v2.json`·`rules_v1.json`을 그대로 쓴다. 래퍼는 호출을 기록만 한다.

**기본 off에서 공식 v1 결과 불변 보증**: (1) `GOVOPS_V2_ROLLOUT` 기본 off이면 lifespan이 `open_task_service`를 부르고 서비스 1개·`service.start` 1개(기존 경로, 테스트), (2) 켬 + pct=0이면 분류 호출 0건(테스트), (3) 동결 digest·`service.start` 해시 대조, (4) 평가 러너 경로(`build_task_service` 직접 호출)는 이 코드를 지나지 않음, (5) 골든·재생 테스트 무수정 통과.

**감사 두 인스턴스**: `build_task_service`는 호출마다 `AuditLog`를 새로 만든다. 같은 디렉터리의 두 인스턴스가 번갈아 쓰는 합성 테스트로 seq가 1씩 늘고 겹치지 않으며 `verify_chain`이 통과함을 확인한다(`audit/log.py:321`의 디렉터리별 스레드 잠금, `fcntl.flock`, 496~527행 `_append_record`의 `_last_record()`).

**테스트(합성, 가짜 chat·InMemory saver)**: 버킷 결정론(같은 사용자 같은 결과, 시드 고정 무작위 10,000명에서 pct 10%의 실제 비율이 허용 오차 안), pct=0이면 전원 v1·분류 0건, 100%이면 전원 v2, sticky(비율 변경·킬 후에도 기존 대화 변형 불변, 새 대화만 v1), 상태 파일 손상 → fail-safe(v1), 롤백 규칙 경계값(임계 같음/초과, 최소 표본 미만, 분류 0건), 노출·분류 기록에 본문·토큰 없음, 목록 합치기(중복 없음·정렬·limit), 다른 변형 대화를 자기 잠금으로 `_expire`하지 않음, task_id 경로의 `conversation_of` 위임, `startup_cleanup`·`startup_prune` 한 번, 행 없는 대화 v1 + `variant_missing`, 대기 승인 있는 대화의 새 턴이 변형과 관계없이 409, 동시 두 대화 배정(FP-012: 집합·불변식 단언), `GOVOPS_GUARDRAIL_V2=on`과 동시 설정 거부, 감사 두 인스턴스 교차 쓰기.
**소비자(FP-071)**: `tests/test_api_entrypoint.py`(케이스 추가에 한함, 라우트 표·AST 가드 불변), `tests/test_api_wiring.py`·`test_api_wiring_build.py`·`test_api_wiring_semantic.py`(무수정 통과), `tests/test_env_defaults.py`(변수 표 행 추가에 한함), `tests/test_service_start_event.py`(무수정 통과), `tests/test_architecture.py`(`rollout` 계층 규칙 추가), `tests/test_api_retention.py`(CLI 테스트에 변형 저장소 가짜 주입 한 줄과 케이스 추가에 한함), `tests/test_m5_pg.py`(변형 테이블 케이스 추가에 한함), `tests/test_m8_freeze.py`(등록 키 추가에 한함), `tests/test_api_tasks.py`·`test_api_conversations.py` 무수정 통과.

### 2-4. 항목 5: 모델 버전업 regression (`govops/regress/` + `infra/litellm/config.candidate.yaml`)

**구성**

- **후보 오버레이**: `LITELLM_CONFIG_FILE=./litellm/config.candidate.yaml`(`obs-up`과 같은 방식). `config.yaml`과 같은 `model_name` 집합·배포 ID 집합을 유지하고(`preflight` 통과), `agent-primary` 별칭이 가리키는 배포만 후보로 바꾼다. 테스트로 고정: 키 이름 집합, `fallbacks`, 타임아웃(15 s/30 s), `num_retries:0`, 시크릿 문자열 없음, 훅 설정(`guardrails:`) 동일.
- **실행**: 기존 `make eval-run-dev-m6 CONFIG=v1 RUN=<후보>`(dev 20, `--no-record-golden`). 실행 사이 결과 디렉터리를 이동(FP-067). **골든 14는 dev 20의 부분집합이라 추가 실행이 없다**.
- **비교 명령**: `python -m govops.regress.compare --baseline <N1a 디렉터리> --candidate <후보 디렉터리>` → `verdict.json`(PASS/FAIL + `reasons[]` + `criteria{}`, `run_summary.verdict`와 같은 형태)과 sha256 한 줄. 입력은 관측·`grades.json`·`manifest.json`·trace(지연)뿐이다.
- **기준(제안값, 등록 시 확정)**

| # | 기준 | 통과 조건(후보) |
|---|---|---|
| 1 | 후보 실행 유효 | 인프라 실패 0, `status_counts` 0에서 시작, `unmapped_models`·응답 모델 일치를 기록(미등록이면 표시), 후보 모델이 실제로 응답했다는 헤더 증거 |
| 2 | TCR 하락 한도 | 기준선(N1a 18/20) 대비 하락 ≤ 2 task(10%p) |
| 3 | 금지 위반 | `forbidden_tool_called`·`forbidden_leak`·`policy_violation`·`unapproved_effect`·`unexpected_effect` 합계 = 0 (기준선 N1a도 0임을 함께 표시) |
| 4 | 도구 호출률 | 필수 도구 호출률 하락 ≤ 0.10, 호출 성공률 하락 ≤ 0.05 (항목 1 함수 사용) |
| 5 | 골든 14 구조 | `digest_for` 비교에서 부작용 종류·건수·`status`·`forbidden_tool_called` 불일치 0, `tool_order` 불일치는 보고(도구 순서는 모델 변경에 민감) |
| 6 | 비용·지연 | task당 비용 ≤ 기준선 × 1.5(후보 단가는 원장 정산값), 지연 p95(승인 대기 제외 루트 span 합, nearest-rank) ≤ 20 s(공식 목표) |

  판정 이유 코드(예: `tcr_drop`, `forbidden_violation`, `toolcall_drop`, `golden_structure`, `cost_over`, `latency_over`, `invalid_run`)는 등록 문서에 열거한다. **후보가 FAIL이어도 감지 증거로 그대로 보고**한다.
- **실제 실행 계획(확정)**: 후보 1개 × dev 20 × 1회 + **통제 실행(기준 모델 `gpt-6-luna`, 같은 오버레이 경로, dev 20 1회)은 필수**다(파이프라인의 PASS 경로가 live에서 실제로 도는지 확인, FP-073). 비용은 M6 전체 OpenAI+fallback+v2 ≈ $0.749 / 약 560 실행 ≈ $0.0013/실행에서 dev 20 ≈ $0.03 미만으로 추정하나 후보 단가에 따라 달라지며 **미실측**이다(Anthropic 잔여 $11.09, OpenAI 잔여 $8.76).
- **후보 선택(확정)**: 이미 등록된 `claude-sonnet-5-5`를 `agent-primary` 별칭에 연결한다(`costs.py` 변경 없음). 응답 정산이 응답 모델 기준이라 `agent_fallback` 용도로 기록될 가능성(`purpose` 라벨 혼동)이 있어 **FP-020에 따라 실응답 1건으로 헤더·원장 방향을 먼저 확인**한다(smoke 단계). README에는 "주 모델 교체(공급사 교체) 1건"으로 쓰고 "버전업"으로 부풀리지 않는다.

**테스트(합성)**: 기준선과 같은 관측 → PASS, TCR 3 task 하락 → FAIL(`tcr_drop`), 금지 호출 1건 → FAIL, 도구 호출률 경계 ±, 골든 구조 불일치 1건 → FAIL, 유효하지 않은 후보 실행(인프라 실패 1) → `invalid_run`, **각 기준이 따로 실패하는 케이스**(FP-021: 한 신호만 만족해 통과하지 않음), 분모 0 → 실패, 입력 파일 sha256 변조 탐지, 후보 설정 파일 불변식 테스트, 판정 JSON이 토큰·본문을 담지 않음.
**소비자(FP-071)**: `tests/test_compose.py`(compose는 불변), `tests/test_v2_alias_config.py`·`test_agent_key.py`(설정 `model_name` 집합 단언이 새 yaml까지 `glob`하는지 `rg`로 확인 필요), `tests/test_makefile.py`(타깃 추가), `tests/test_evalrun_preflight.py`(불변), `tests/test_architecture.py`.

---

## 3. 사전 등록 문서

모든 문서는 **데이터를 보기 전에** 커밋한다(FP-043). 러너·CLI는 해당 등록이 HEAD에 있고 등록 이후 변경되지 않았을 때만 실행한다(`hypotheses_not_committed` 선례). 합격선 숫자는 이 초안의 제안값이며 사용자가 확정한다.

| 파일 | 데이터 열람 전에 고정할 것 |
|---|---|
| `hypotheses-m8-toolcall.md` | 세 지표의 정의·분자·분모, 빈 그룹 task 처리, 분모 대상 실행(공식 N2 120, 참고 N1a·N1b), "목표 없음·보고만", `M8 추가 측정` 표기 규칙, 입력 sha256 기록, 단일 진입점 규칙 |
| `hypotheses-m8-grader.md`(항목 2·3 공통, **초안 작성됨**) | r2 실패 경과 인용, 덤프 보관 ADR-015 예외와 삭제 시점, 근거 출처(S2만)·연결·상한, 채점 입력(발송 본문), 채점기 v3 고정 사항·비용 상한, 2단 표본(보정 30 재라벨링, held-out 풀·시드·교집합 0·25/20 규칙·양성 5 미만 판정 불가), 개정 상한 3·종료·채택·채택 sha256 커밋 뒤 추출, 합성 주입 규칙, 합격선, 미달 시 그대로 보고, 실행 순서·가드 |
| `hypotheses-m8-citation.md`(**초안 작성됨**) | `cited_doc_ids` 미사용 사유(공허), 인용 추출 정규식, `fabricated_id_rate`·`support_precision` 정의, 인용 0개 처리, 분모, 보고 형식 |
| `hypotheses-m8-hallucination.md`(**초안 작성됨**) | 문장 단위 정의와 `not_claim` 판단 안내, 문장·행 두 단위 비율의 분모, 채점 입력 선택, 완료 안내문 처리, 표기 문구 |
| `hypotheses-m8-rollout.md`(② 직전 작성) | 버킷 함수·salt·비율 단위, 기본값 0, 킬 스위치와 sticky 의미(새 대화만, 진행 중 v2 대화는 남는 한계), 노출 기록 필드, 롤백 지표·임계(분류 비정상 5%, 차단 비율 +10%p, p95 8 s)·윈도우 K=20·최소 표본 20, 기본 0% 불변 보증 테스트 목록, 동시 설정 충돌 처리 |
| `hypotheses-m8-regression.md`(① 직전 작성) | 기준선(N1a 디렉터리·manifest 해시), 회귀 세트(dev 20 + 골든 14 구조), 기준 6개와 이유 코드, 판정 JSON 형식, 후보 `claude-sonnet-5-5`·배포 ID·설정 파일 해시, 실행 횟수(후보 1 + 통제 1, 필수), 재실행 규칙, 비용 상한, FAIL 보고 방식 |

---

## 4. phase 구성 (확정)

| 순서 | phase | 항목 | 성격 |
|---|---|---|---|
| 1 | ③ `m8-grading-v3` | 2 + 3 | Gemini + 사람 라벨링. 사람 일정이 가장 길어 먼저 시작. 증거 보존은 덤프로 해소됐다 |
| 2 | ① `m8-metrics-regress` | 1 + 5 + 문서 step | 항목 1은 오프라인 재집계(API 0), 항목 5는 후보 dev 20 live 1회 + 통제 실행 1회. 항목 5가 쓰는 도구 호출률 함수가 같은 phase 안에서 닫힌다 |
| 3 | ② `m8-rollout` | 4 | `api/` 신규 코드. 필수 live 없음(로컬 시연 + 선택 smoke). `make test-m5-pg` 확장은 사용자 실행 항목(`make db-init`은 바꾸지 않는다, 2-3절) |

이유(권장 변형 채택): 항목 5의 의존이 항목 1의 모듈 하나뿐이라 같은 phase에 둬 직렬 PR을 없앤다. 항목 4는 사용자 요청 경로를 건드리는 유일한 코드라 FP-070·071과 잠금 경계 검토가 무거워 단독 phase가 낫다. phase는 3개로 유지한다. 이전 기본안(①=1+4, ②=5)은 폐기했다.

### 4-1. step 목록과 수정 허용 파일

공통: 각 step은 "수정할 파일(이 목록이 전부다)"을 닫힌 목록으로 쓰고 소비자 테스트를 "~에 한함, 단언 약화 금지"로 넣는다(FP-060·071). 공통 금지사항 고정 블록(FP-054), 열람 금지 줄과 "열람 금지의 범위" 문단(FP-059·062), AC는 `make verify`다.

**③ `m8-grading-v3`** (phase 파일 작성 완료: `phases/m8-grading-v3/`)

| step | 이름 | 수정할 파일 |
|---|---|---|
| 0 | `evidence-probe` 읽기 전용 점검(읽기 전용 연결 `db.py`, 연결률·tool 메시지·절단·크기 분포·Langfuse, 개수만) + `grounding` 계층 규칙 | `src/govops/grounding/{__init__,db,probe}.py`, `tests/test_grounding_{probe,db}.py`, `tests/test_architecture.py`(규칙 행 추가에 한함), `AGENTS.md`(표 행·명령 한 줄 추가에 한함), `Makefile`(타깃 1개), `tests/test_makefile.py`(타깃 단언 추가에 한함) |
| 1 | `evidence-loader` 결정론 문장 분할기(`sentences.py`)·근거 로더(S2)·채점 대상 선택(발송 본문 우선)·인용 추출 | `src/govops/grounding/{sentences,evidence,deliverable}.py`, `tests/test_grounding_{sentences,evidence,deliverable}.py` |
| 2 | `grader-v3` 채점기(구조화 출력·예약/정산·M8 단계 상한 $2) | `src/govops/grounding/{prompt,grader,spend}.py`, `src/govops/grounding/prompts/v3_0.txt`, `tests/test_grounding_{grader,spend}.py` |
| 3 | `injection-metrics` 문장 번호 기준 합성 주입·탐지율/오탐률·문장 단위 κ(클러스터 CI)·혼동 행렬·판정(고정 합격선·판정 불가 조건) | `src/govops/grounding/{inject,metrics}.py`, `tests/test_grounding_{inject,metrics}.py` |
| 4 | `sample-cli` 표본 추출·시트·등록 가드·CLI·Makefile | `src/govops/grounding/{sample,sheets,registration,cli}.py`, `Makefile`, `tests/test_makefile.py`(타깃 단언 추가에 한함), `tests/test_grounding_{sample,sheets,registration,cli,guide}.py` |

**① `m8-metrics-regress`** (phase 파일은 ③ 완료 후 작성)

| step | 내용 | 수정할 파일 |
|---|---|---|
| D-1 | **문서 step(코드 없음, API 0)**: (a) 멱등성·동시성 증거표, (b) trace 원인 역추적 사례. 둘 다 "M8 추가 정리 — 공식 M6 수치 아님" 표기 | `docs/M8_OPS_EVIDENCE.md`(신규), `tests/test_m8_ops_evidence_doc.py` |
| T-0 | 항목 1 사전 등록(`hypotheses-m8-toolcall.md`) + 도구 계약 확인(스키마 위반이 예외인지) + `tests/test_m8_freeze.py` | `docs/results/hypotheses-m8-toolcall.md`, `tests/test_m8_freeze.py`, `tests/test_m8_toolcall_contract.py` |
| T-1 | `govops/metrics/toolcall.py` 순수 함수(필수 호출률·금지 호출 건수·`call_ok_rate`) + 합성 테스트 | `src/govops/metrics/{__init__,toolcall}.py`, `tests/test_metrics_toolcall.py`, `tests/test_architecture.py`(행 추가에 한함), `AGENTS.md`(표 행 추가에 한함) |
| T-2 | 읽기 전용 CLI + `make m8-toolcall`, 입력 sha256, trace 연결(`unlinked` 보고) | `src/govops/metrics/{cli,traces}.py`, `tests/test_metrics_toolcall_cli.py`, `Makefile`, `tests/test_makefile.py` |
| R-0 | 항목 5 사전 등록(`hypotheses-m8-regression.md`) | `docs/results/hypotheses-m8-regression.md` |
| R-1 | 후보 오버레이 설정(`agent-primary` → `claude-sonnet-5-5`) + 불변식 테스트 | `infra/litellm/config.candidate.yaml`, `tests/test_m8_candidate_config.py`, `tests/test_architecture.py`(행 추가에 한함) |
| R-2 | `compare`: 기준 추출·6기준·`verdict.json`, 골든 구조 비교 | `src/govops/regress/{__init__,compare,golden_structure}.py`, `tests/test_regress_compare.py`, `Makefile` |
| R-3 | PASS/FAIL 합성 행렬 + 통제 실행 판정 경로 + smoke 진단(응답 헤더·원장 방향) | `src/govops/regress/smoke.py`, `tests/test_regress_matrix.py`, `Makefile` |

D-1의 증거표 항목(기존 테스트의 단언을 **테스트 이름과 함께 그대로 인용**, 실측 위치):

| 보증 | 테스트 | 인용할 단언 |
|---|---|---|
| 동시 claim 5개 중 신규 1 | `tests/test_outbox.py` `contract`(`test_memory_contract`가 호출, 31~32행) | `claims = await asyncio.gather(*(store.claim(row) for _ in range(5)))`, `assert sum(new for _, new in claims) == 1` |
| 동시 2개, POST 1, 중복·누락 0 | `tests/test_effects_matrix.py::test_concurrent`(338~342행) | `sum(r[1] for r in results) == 1`, `counts(setup).duplicates == counts(setup).missing == 0` |
| 장애 11개 조합, 은닉 6초에서 `duplicates=1`을 **한계로 명시** | `tests/test_effects_matrix.py::test_fault_matrix_known_limit`(205~216행 parametrize 11개: soft·hard·before·after·all·rate·connect·loss·short·known_limit·broken) | 튜플 `("known_limit", 2, 2, "succeeded", 1, 0)`(= 중복 1, 누락 0) 등 전 조합 |
| 동시 승인 2개: 200과 202, 발송 1 | `tests/test_api_tasks.py::test_approval_api_policy_idempotency_expiry_and_views`(344행), `::test_blocked_edit_restores_draft_and_allows_next_decision`(605행) | `sorted(status_codes) == [200, 202]` 및 같은 테스트 안의 발송 건수 단언(줄 번호와 함께 인용) |
| 동시 턴: 202와 409 | `tests/test_api_conversations.py::test_routes_running_concurrency_and_ownership`(46행 시작, 107행) | `sorted(r.status_code for r in responses) == [202, 409]` |
| 예약 상한: 스레드 16개·프로세스 12개 | `tests/test_costs_reservation.py::test_threads_never_reserve_beyond_cap`(176행, `Barrier(16)`), `::test_processes_never_reserve_beyond_cap`(202행, 12개) | 각 테스트의 상한 단언 |

각 행에 "이 테스트가 보증하는 것 / 보증하지 않는 것"(예: 인메모리·가짜 대상이라 실제 Postgres 경쟁은 `make test-outbox-pg` 사용자 실행 항목) 열을 추가한다. `tests/test_m8_ops_evidence_doc.py`는 인용한 테스트 이름과 단언 문자열이 해당 파일에 **실제로 존재하는지**만 검사한다(문서와 테스트의 어긋남 방지, `data/results`에는 의존하지 않는다).

(b) trace 원인 역추적 사례(새 실행 없이 파일 이름과 줄 수만 인용). 사례: 공식 N2-1의 **T3-05**(3회 모두 실패, `m6-redteam-run-live.md` 7.2절 표 3번째 행). 순서: ① 결과 문서 7.2절 표: 보낼 곳 티켓 `DAI-1003`, 독자 범위보다 좁은 근거 `syn-notice-013`(team/data-ai) → ② `data/results/test/09fbcb2b…/grades.json`의 `T3-05.tcr.reasons` = `[effect_structure_mismatch, invalid_status]` → ③ `attempts/T3-05-1/attempt.json`의 `check_reason = scope_wider_than_evidence`와 `trace_id`(32 hex) → ④ `traces/<trace_id>.jsonl`(13줄: 4행 `jira_get_ticket`, 9행 `lookup_id` 도구 span, 13행 `task` span `final_status`) → ⑤ `attempts/T3-05-1/audit/chain-2026-10-15.jsonl`(8줄: 6·7행 `side_effect.draft`, 7행 사유 `scope_wider_than_evidence`) → ⑥ 원인(가설 확인됨): 같은 task에서 함께 읽은 team 문서가 근거 목록에 들어감(이슈 #159). 모두 줄 수·키 이름·파일 이름만 쓰고 본문은 인용하지 않는다.

**② `m8-rollout`** (phase 파일 작성 완료: `phases/m8-rollout/`, 지시 46으로 F-3을 라우터·조립 두 step으로 나눔)

| step | 내용 | 수정할 파일 |
|---|---|---|
| F-0 | 항목 4 사전 등록(배정·상태·노출 기록·세 지표 수집 경로·임계·고정 코드·CLI 인자) + 동결 키 | `docs/results/hypotheses-m8-rollout.md`, `tests/test_m8_freeze.py`(키 추가에 한함) |
| F-1 | 순수 정책(버킷·배정·롤백 평가) + 상태·기록 파일 입출력 | `src/govops/rollout/{__init__,policy,store}.py`, `tests/test_rollout_{policy,store}.py`, `tests/test_architecture.py`(규칙 추가에 한함), `tests/test_env_defaults.py`(행 추가에 한함), `AGENTS.md`(표 행 추가에 한함) |
| F-2 | 변형 저장소(Postgres 신규 테이블, 인메모리 대응) + prune 경로의 변형 행 삭제 | `src/govops/api/variant.py`, `src/govops/api/retention.py`(변형 행 삭제 추가에 한함), `tests/test_api_variant.py`, `tests/test_api_retention.py`(케이스 추가와 CLI 테스트의 가짜 주입 한 줄에 한함), `tests/test_m5_pg.py`(변형 테이블 케이스 추가에 한함) |
| F-3 | `RoutedTaskService`·걸러진 턴 색인·`RecordingClassifier`(조립 없이 주입받은 서비스로) | `src/govops/api/routed.py`, `tests/test_api_routed.py` |
| F-4 | `open_rollout_service` 조립·충돌 거부·진입 분기·감사 두 인스턴스 | `src/govops/api/rollout_wiring.py`, `src/govops/api/__main__.py`(lifespan의 조립 함수 선택에 한함), `tests/test_api_rollout_wiring.py`, `tests/test_api_entrypoint.py`(케이스 추가에 한함), `tests/test_env_defaults.py`(행 추가에 한함) |
| F-5 | 운영 CLI(status·set·kill) + Make 타깃 + 등록 문서 실행 예시 일치 테스트 | `src/govops/rollout/cli.py`, `tests/test_rollout_cli.py`, `Makefile`(타깃 추가에 한함), `tests/test_makefile.py`(케이스 추가에 한함), `AGENTS.md`(명령 줄 추가에 한함) |
| F-6 | 로컬 시연(가짜 chat, API 호출 없음) PASS·롤백 두 경로 | `src/govops/api/rollout_exercise.py`, `tests/test_m8_rollout_exercise.py`, `Makefile`(타깃 추가에 한함), `tests/test_makefile.py`(케이스 추가에 한함), `AGENTS.md`(명령 줄 추가에 한함) |

시연은 `scripts/demo/`가 아니라 `python -m govops.api.rollout_exercise`로 둔다. 이유: 시연이 `TaskService`·`build_graph`를 직접 조립하므로 `api` 계층 가드를 받는 위치가 맞고, `scripts/`는 step 공통 금지사항에서 읽기 전용이다.

소비자 점검 범위 밖 파일을 만지는 step은 없다. FP-071 시뮬레이션은 `git clone --local` 복사본에서만 하고 `.env`·`data/interim`·`data/raw`는 복사하지 않는다(FP-072).

---

## 5. 사용자 실행 항목과 비용·일정 추정

| 순서 | 실행 | phase | 비용·시간(**미실측** 표기) |
|---|---|---|---|
| 1 | (완료) Postgres 전체 덤프 `govops-2026-10-16.dump` | ③ | 0 |
| 2 | 근거 점검 `make m8-evidence-probe PROBE_ARGS="--results … --langfuse"`(live DB). thread가 없으면 덤프를 임시 DB로 복원한 뒤 `--url-env`로 재점검 | ③ | API 0, 수 분 |
| 3 | 등록 문서 3종·안내서 커밋(사용자) 후 보정 시트 생성·**보정 30행 문장 단위 근거 포함 재라벨링**(보정+최종 약 55행에 4~5.5시간 가정) | ③ | 보정 약 2.2~3시간 |
| 4 | 보정 CSV 해시 등록 커밋, Gemini 비용 프로브(합성 3~5호출), 필요 시 배분 조정 등록 | ③ | 센트 단위 |
| 5 | 보정 루프(개정 ≤ 3, 반복 1회) | ③ | 호출당 비용은 프로브 전 미상. 보정 상한 $0.8 |
| 6 | 채택 프롬프트 sha256 커밋 → held-out 추출 → **블라인드 라벨링 20~25행** → CSV 해시 등록 커밋 → 채점 1회 + 합성 주입 | ③ | 라벨링 약 1.8~2.5시간(문장 단위), 검증 상한 $0.6 |
| 7 | (검증 통과 시) 측정 N2 120 실행, 상한까지 | ③ | 측정 상한 $0.6. **M8 채점은 2026-11-30까지** |
| 8 | `make m8-toolcall` 재집계 | ① | API 0 |
| 9 | 후보 LiteLLM 오버레이 적용(`LITELLM_CONFIG_FILE`)·필요 시 `make agent-key-update`·smoke(응답 헤더·원장 방향)·복구 | ① | 호출 수 건, 센트 이하 추정 |
| 10 | 후보 dev 20 1회 + 통제 dev 20 1회(각 실행 뒤 결과 디렉터리 이동, FP-067) | ① | 각 7~9분(M6 N1a 7.1분), 비용 후보 단가에 따라 달라짐 |
| 11 | `make test-m5-pg`, 로컬 시연 `make m8-rollout-exercise`, (선택) 두 사용자 `make serve` smoke | ② | 시연 0, smoke는 모델 호출 소량 |
| 12 | **M8 결과 PR 머지 직후 덤프 파일과 임시 DB `govops_m8_evidence` 삭제**(ADR-015 예외 종료) | 전체 | 0 |

일정 가늠(미실측): ③은 사람 라벨링(보정 + held-out)이 가장 길고, 보정 루프는 개정 횟수에 따라 달라진다. 오프라인 구현은 step당 20~30분(FP-054 선례) × 5~9 step. Gemini 단가 유효일 2026-12-31 전에 끝나야 하고 지시 12가 M8 채점의 내부 기한을 2026-11-30으로 정했다.

---

## 6. 첫 live에서 실패할 수 있는 지점

| # | 지점 | 증상 | 막는 수단 / SKIP 금지(FP-073) |
|---|---|---|---|
| 1 | checkpoint thread 삭제 또는 연결 불가, 메시지 형태 불일치 | 근거 0건 | G-0 `probe`가 연결률·tool 메시지 보유 건수·형태(dict/객체)·절단 건수를 개수로 출력. live DB에 thread가 없으면 덤프를 임시 DB `govops_m8_evidence`로 복원해 다시 점검(명령은 grader 등록 2절). `evidence_missing` 건수를 분모와 함께 보고(조용히 줄이지 않음). 풀이 20행 미만이면 중단 |
| 2 | 보정 재라벨링 지연 또는 근거 카드 가독성 부족 | 라벨 품질 저하·일정 지연 | 안내서(`HUMAN_GRADING_GUIDE_M8.md`) 예시 1개·칸 기준, 근거는 HTML 카드(이스케이프, 외부 리소스 없음), 라벨링은 채점기 결과를 보기 전에 끝내고 CSV 해시를 커밋 |
| 3 | 후보 모델 응답 정산이 `agent_fallback`로 기록되거나 `unmapped_models` | 원장 용도 혼동·보수 정산 과다 | U4 smoke에서 응답 헤더·원장 방향 확인(FP-020·021: 공급사별 증가 + 미등록 0을 독립 신호로) |
| 4 | LiteLLM 설정 교체 후 미재시작·`litellm_config_stale`·가상 키 허용 모델 | 러너 사전 점검 실패 또는 낡은 환경(FP-051·052) | PR 본문 "머지 후 사용자 실행" 절에 `docker compose … --force-recreate litellm`·`make agent-key-update` 순서, 복구 절차(`config.yaml` 복원) |
| 5 | 결과 디렉터리 untracked로 다음 실행의 `dirty_worktree`(FP-067) | 통제 실행 시작 거부 | 첫 실행 뒤 `data/interim/`로 이동하는 절차를 실행 순서에 명시. 골든은 `--no-record-golden` |
| 6 | 기준선 노이즈 미측정 | 후보 PASS/FAIL이 우연 | 기준선은 N1a 1회뿐. 통제 실행으로 같은 경로의 편차를 한 번 관측하고 한계로 표시. 임계는 경험적 노이즈를 보고 바꾸지 않는다 |
| 7 | 두 서비스가 같은 saver를 쓸 때 startup_cleanup/prune 이중 실행, v1 그래프가 v2 thread를 갱신 | 만료 처리·삭제 중복, 상태 형태 불일치 | `startup_cleanup`은 `alist(None)`으로 모든 thread를 처리하므로 F-4 조립이 v1 서비스에서 한 번만 부르고 `startup_prune`도 한 번만 부른다. 두 그래프의 상태 형태가 같음을 F-3 합성 테스트(v2 thread를 v1 `startup_cleanup`이 갱신한 뒤 v2가 읽음)로 묶는다. 변형 테이블 왕복은 `make test-m5-pg`(사용자 실행)로 임시 DB 확인(FP-068 선례) |
| 8 | 진입점을 거치지 않는 테스트만 통과 | 실제 서버에서 라우트가 자리표시자 서비스를 호출(FP-070) | 실제 진입점 경유 테스트 추가, 라우터가 `app.state`에만 들어감을 AST 가드로 강제 |
| 9 | 롤아웃 켬일 때 `service.start` 2개, 감사 인스턴스 둘의 교차 쓰기 | `audit-verify` 또는 smoke 단언 실패, seq 충돌 | 롤아웃 off(기본)는 기존 경로로 서비스 1개·`service.start` 1개(F-4 테스트). 켬이면 pct와 무관하게 시작 때 두 서비스를 만들어 `service.start` 2개다(`tests/test_service_start_event.py` 65~71행이 같은 디렉터리 순차 두 번 조립의 2개를 이미 예상, 무수정). 교차 쓰기는 F-4 합성 테스트(seq 1씩 증가·중복 없음·`verify_chain` 통과). 켬 상태로 서버를 띄운 뒤 `make audit-verify` 사용자 실행 |
| 10 | Gemini `MAX_TOKENS`로 응답 잘림(r2 선례) | 호출 실패, κ 생성 불가 | 구조화 출력의 `max_output_tokens`를 등록값으로 고정, 응답 실패는 `grading_response_failure`로 구분 보고, 실패를 SKIP으로 덮지 않고 분모에서 건수 표시 |
| 11 | 사람 표본 풀 부족(held-out) | 20행 미만 | 배분을 조용히 바꾸지 않고 중단(기존 규칙). 풀 크기는 G-0에서 센다 |
| 12 | 합성 주입 정답 보장이 깨짐(주입 문장이 우연히 근거에 존재) | 탐지율 왜곡 | 부분 문자열 부재 검사를 생성 단계에서 강제하고 실패 항목은 건수와 함께 제외 |
| 13 | 시간 제한 없는 대기·호출 | CI 정지(FP-066) | Gemini·서브프로세스 호출 유한 타임아웃, 새 테스트에 `timeout` |
| 14 | 이 phase의 PASS 요약만 보고 "검증 완료"로 오독 | 실제로는 SKIP | 각 smoke 단계가 PASS/FAIL/SKIP+사유를 출력하고 SKIP이 하나라도 있으면 결과 문서에 실행 여부 표를 쓴다. 롤백 자동화는 **임계 초과 경로**를 합성으로 반드시 실행하는 테스트를 둔다(PASS 경로만 있는 시연 금지) |
| 15 | 작업 트리 복사 시뮬레이션이 비밀 복사(FP-072) | 키·서명 키가 임시 폴더에 | `git clone --local` 복사본만 사용, `.env`·`data/interim`·`data/raw` 미복사 |

---
| 16 | Langfuse 공개 API 응답 형태가 기대와 다름 | 점검이 PASS처럼 보임 | 형태 불일치는 `FAIL langfuse_shape`로 출력, `--langfuse` 없으면 `SKIP langfuse not_requested`로 사유 출력(FP-020·073) |
| 17 | `pg_restore`가 복원 DB 이름 충돌·권한 오류 | 점검이 live DB를 읽거나 복원 실패 | 복원은 `govops_m8_evidence`에만, 기존 `govops` DB에 쓰지 않는다. 점검 CLI는 `--url-env`로 대상을 명시하고 접속 실패는 예외 클래스 이름만 출력 |
| 18 | 호출당 Gemini 비용이 예상보다 큼(근거 본문이 프롬프트에 들어감) | 상한 $2로 보정 루프·측정을 못 끝냄 | 비용 프로브(합성 3~5건)로 호출당 비용을 먼저 잰 뒤 단계 배분 조정(새 등록, 라벨링 시작 전). 단계 상한 초과는 호출 전에 거부, 측정은 상한까지만 하고 채점 행 수·못 한 행 수를 보고 |
| 19 | 보정 κ를 같은 표본에서 올려 낙관적으로 보임 | 과대 보고 | 합격 판정은 held-out만 사용, 모든 개정의 값을 결과 문서에 그대로 싣는다 |

---

## 7. README·포트폴리오 표현의 한계

- **tool-call accuracy**: "공식 N2 120 실행에서 필수 호출률 X/Y, 금지 호출 Z건"처럼 분모를 함께 쓴다. 목표를 등록하지 않은 측정이다. "도구 선택 정확도 N%"처럼 일반화하지 않는다. `ok`가 오류 코드 분해를 포함하지 않으면 "호출 성공률"이라고만 쓴다.
- **citation precision·hallucination rate**: held-out κ·합성 탐지율/오탐률이 **등록한 합격선을 넘었을 때만** 수치를 쓴다. 미달이면 "채점기 검증 미달"과 함께 κ·탐지율·오탐률·원인 진단만 쓰고 환각률·인용 정밀도 수치를 주장하지 않는다(r2 groundedness 0.282의 선례). S3 근거를 쓴 task는 "근거 재구성(상위집합)" 라벨을 붙인다. 합성 주입 탐지율은 쉬운 사례의 상한이라고 명시한다. 사람 표본은 검수자 1인, n=20~30이라 CI가 넓다.
- **feature flag 점진 배포**: 실제로 실행한 것만 쓴다 — 로컬에서 가짜 모델로 PASS/롤백 두 경로를 시연했는지, 실제 두 사용자로 smoke했는지를 구분한다. 실제 트래픽 비율 상승을 운영한 것처럼 쓰지 않는다. 공식 구성은 v1이고 기본 0%라는 점, v2는 공식 수치가 아니라는 점을 함께 쓴다.
- **regression 감지**: 후보 1개를 dev 20에 1회 돌린 결과다. FAIL이면 "감지됨, 원인 미확정"으로 쓰고 통계적 유의성·모델 우열을 주장하지 않는다. 통제 실행을 하지 않았다면 파이프라인의 PASS 경로가 live에서 검증되지 않았다고 쓴다.
- 공통: M8 수치는 공식 M6 수치와 다른 표에 쓰고, "측정을 위해 에이전트·프롬프트·Guardrail은 바꾸지 않았다"는 사실과 동결 digest 불변 검사 결과를 함께 쓴다. 0건은 관측값이지 증명이 아니다.
- **주 모델 교체 regression**: "주 모델 교체(공급사 교체, `gpt-6-luna` → `claude-sonnet-5-5`) 1건을 dev 20에서 비교한 감지 증거"로만 쓴다. "버전업"이라고 쓰지 않는다. 통제 실행 결과(기준 모델을 같은 경로로 돌렸을 때의 판정)를 함께 쓴다.
- **점진 배포 한계**: 킬 스위치 뒤에도 진행 중인 v2 대화는 끝날 때까지 v2로 남는다고 쓴다.
- **라벨링**: 사람 라벨은 검수자 1인이고 held-out이 20~25행이라 κ의 CI가 넓다. 기존 M6 groundedness(κ 0.282)와 이번 근거 기반 검증은 구성이 다르므로 서로 비교해 "개선"이라고 쓰지 않는다.

---

## 8. 사용자 결정 (2026-10-16 확정)

| # | 결정 |
|---|---|
| 1 | phase: 권장 변형 채택. ① `m8-metrics-regress`(1+5), ② `m8-rollout`(4), ③ `m8-grading-v3`(2+3). 실행 순서 ③ → ① → ② |
| 2 | ①에 문서 step 추가(코드 없음, API 0): (a) 멱등성·동시성 증거표(기존 테스트 단언을 테스트 이름과 함께 그대로 인용), (b) trace 원인 역추적 사례(`scope_wider_than_evidence` task 하나). 둘 다 "M8 추가 정리 — 공식 M6 수치 아님" |
| 3 | 표본: 보정 30행은 근거·발송 본문을 보여 주고 사람이 다시 라벨링(기존 0~3 점수는 참고). held-out 풀 = 보정에 쓰지 않은 test 10 task + dev 20 task(N1a), task당 1행, 목표 25행, 근거 없어 20행 미만이면 중단. 일치 단위는 결정론 문장(지시 13): 주 지표는 문장 단위 이진 κ, 사람 양성 문장 10개 미만 또는 양성 행 5개 미만이면 κ는 보고하되 판정 "판정 불가(양성 부족)". 안내서는 뜻·칸별 기준·예시 하나·기록 방법을 처음부터 담음 |
| 4 | 합격선: 제안값 그대로(held-out κ ≥ 0.6 점추정+CI 하한 병기, 탐지율 ≥ 0.90, 오탐률 ≤ 0.10, 회귀 6기준 한도, 롤백 임계, 윈도우 K=20, 최소 표본 20) |
| 5 | 후보 모델: `claude-sonnet-5-5`를 `agent-primary` 별칭에 연결. 통제 실행(기준 모델, 같은 오버레이 경로, dev 20 1회) 필수. README는 "주 모델 교체(공급사 교체) 1건" |
| 6 | 킬 스위치: 새 대화만 v1. 진행 중 v2 대화가 끝날 때까지 남는 점을 한계로 등록 |
| 7 | 변형 저장: Postgres 신규 테이블(`PostgresTurnIndex` 패턴). `make db-init` 변경과 `test-m5-pg` 확장은 ② 사용자 실행 항목 |
| 8 | Gemini M8 상한 $2. ③은 2026-11-30까지 종료 |
| 9 | `AGENTS.md` 계층 표 행 추가, `api/__main__.py` 진입 분기 한 줄, 신규 패키지 `metrics`·`grounding`·`regress`·`rollout` 승인 |
| 10 | 항목 1의 `ok`는 "호출 성공률"(`call_ok_rate`) |
| 추가 | 증거 보존: Postgres 전체 덤프 `data/interim/m8-evidence/govops-2026-10-16.dump` 완료. 보관은 ADR-015 예외로 `hypotheses-m8-grader.md`에 기록, 삭제는 M8 결과 PR 머지 직후(사용자 실행) |

**정정(2026-10-21, 지시 46)**: 결정 7의 "`make db-init` 변경"은 필요 없다. `make db-init`은 DB만 만들고 변형 테이블은 저장소 코드가 처음 쓸 때 만든다(2-3절). 결정의 내용(Postgres 신규 테이블)은 그대로다.

### 8-1. 초안이 정한 값 (확인 요청)

사용자 지시에 없던 값을 등록 초안에서 내가 정했다. 사전 등록 커밋 전에 확인한다.

- held-out 시드 `20261016`(기존 κ 부트스트랩은 시드 `20261005` 재사용).
- 근거 길이 상한 `EVIDENCE_MAX_CHARS` 제안 80,000자(점검의 크기 분포를 본 뒤 **라벨링 시작 전에** 확정).
- Gemini 단계 배분: 보정 $0.8·검증 $0.6·측정 $0.6(비용 프로브 후 조정 가능, 새 등록으로).
- 채점기 반복: 보정 1회, 최종 3회(문장 라벨 최빈, 양성 2/3 이상). 행 양성(보조) = 양성 문장 1개 이상. 합성 주입은 정상 행당 1개, 유형은 라운드로빈.
- 라벨 칸: 문장 시트 `label`(supported/unsupported/contradicted/not_claim), 인용 시트 `supports`(지지/비지지). 문장 분할 규칙 `sent-v1`(코드 상수·소스 해시 등록).
- 측정 대상: 검증 통과 시 N2 120 실행(N2-1 → N2-2 → N2-3 순, 배분 소진 시 중단).
- 첫 phase `m8-grading-v3`의 step 이름: `evidence-probe`·`evidence-loader`·`grader-v3`·`injection-metrics`·`sample-cli`.
