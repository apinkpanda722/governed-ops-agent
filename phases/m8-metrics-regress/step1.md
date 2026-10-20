# Step 1: toolcall-registration

## 읽어야 할 파일

먼저 아래 파일들을 읽고 프로젝트의 아키텍처와 설계 의도를 파악하라:

- `/AGENTS.md`(계층 경계, CRITICAL 규칙)
- `/docs/M8_DESIGN_DRAFT.md`(0-1절, 1절 동결 경계와 1-3절 digest 절차, 2-1절 항목 1, 3절 사전 등록 표, 8절 결정 10번)
- `/docs/results/hypotheses-m8-citation.md`(사전 등록 문서의 형식 선례, 읽기만 한다)
- `/tests/test_m7_freeze.py`, `/scripts/freeze_digest.py`(동결 digest 대조 방식, 읽기만 한다)
- `/src/govops/eval/tcr.py`(`grade_tcr`의 `tool_coverage` 계산), `/src/govops/eval/observation.py`(`ToolCallRecord`), `/src/govops/eval/spec.py`(`required_tools_any`, `forbidden_tools`)
- `/src/govops/evalrun/dev_m3.py`(`_TaskTools.call`, `_EffectCapture`: 관측의 `ok`가 만들어지는 곳, 읽기만 한다), `/src/govops/tools/registry.py`(`ToolRuntime.call`), `/src/govops/agent/graph.py`(도구 호출 처리부 540~590행 부근, 읽기만 한다)
- `/tests/test_tool_registry.py`(가짜 런타임을 만드는 방식)
- `/.claude/skills/failure-patterns/SKILL.md` — 읽기만 한다

## 작업

**수정할 파일**(이 목록이 전부다): `docs/results/hypotheses-m8-toolcall.md`, `tests/test_m8_freeze.py`, `tests/test_m8_toolcall_contract.py`. TDD(계약 테스트를 먼저 쓰고, 그 결과를 문서에 옮긴다).

열람 금지 경로: `data/interim/`, `data/eval/`, `data/redteam/`, `src/govops/guardrails/rules_v1.json`
수정 허용 경로: `docs/results/hypotheses-m8-toolcall.md`, `tests/test_m8_freeze.py`, `tests/test_m8_toolcall_contract.py`

열람 금지의 범위: 이 금지는 **Codex 세션이 해당 경로의 파일을 직접 열람·출력·인용하는 것**에 대한 것이다. 이 step의 코드가 런타임에 그 경로를 다루는 것(예: 기본 출력 경로 `data/interim/m8-metrics/`, 평가 명세를 읽는 `load_specs`)은 대상이 아니다. 테스트가 `data/interim/`·`data/eval/`·`data/redteam/`의 실제 파일 **내용**을 읽는 것은 여전히 금지이며 테스트는 임시 디렉터리·합성 명세·합성 관측만 쓴다. 예외로 `data/results/`의 실행 디렉터리는 파일 이름과 JSON 키 이름을 확인하는 데 한해 열람할 수 있고, 응답 본문(`answer`·`effect_bodies`)이나 근거 본문을 출력·인용하지 않는다. trace의 `output.answer`·`tool.result` 속성 값도 같다. `src/govops/guardrails/rules_v1.json`은 내용을 열거나 출력하지 않는다. 이 제한은 이 step이 새로 작성하는 테스트에 한한 것이며, 기존 `tests/test_m7_freeze.py`가 `scripts/freeze_digest.py`로 해시를 계산하며 동결 경로 파일을 바이트로 읽는 것은 `make verify`의 일부이므로 허용한다. Codex 세션이 그 파일 내용을 출력·인용하는 것은 여전히 금지다.

**목적**: 항목 1(Tool-call accuracy)의 세 지표를 **데이터를 보기 전에** 고정한다(FP-043). 이 step은 코드를 만들지 않는다. 등록 문서는 이 step의 커밋 뒤 동결되며 이후 step(T-1·T-2)이 이 문서의 정의·코드 이름을 그대로 구현한다.

### 1. 실측 사실 (내가 키·값 분포·개수만 확인함. 본문은 보지 않았다)

공식 결과 디렉터리 5개의 구조를 `observations.jsonl`·`grades.json`·`manifest.json`·`traces/*.jsonl`의 키와 값 분포로 확인했다.

| 디렉터리(`data/results/` 아래) | 역할 | `manifest.run_label` / `repeat_index` | 관측 행 | `run_index` 값 | 호출 합계 / `ok=false` |
|---|---|---|---|---|---|
| `test/09fbcb2be83f47669800da400a401e55` | 공식 N2 1회 | N2 / 1 | 40 | 모두 0 | 94 / 0 |
| `test/b330337d70fa4cf9a6c25773fb9b1359` | 공식 N2 2회 | N2 / 2 | 40 | 모두 0 | 98 / 1 |
| `test/bf38e57a74cc49eda2f2be522f14e063` | 공식 N2 3회 | N2 / 3 | 40 | 모두 0 | 101 / 1 |
| `dev/6b3ec0b1ec92402db7ebefb2d4cfb1b4` | 참고 N1a(v1) | N1a / 키 없음 | 20 | 모두 0 | 52 / 0 |
| `dev/6dba4af230d74d8b93a64bf6cbfa0b9a` | 참고 N1b(v1+v2) | N1b / 키 없음 | 20 | 모두 0 | 52 / 0 |

- **"공식 N2 120 실행" = 디렉터리 3개 × task 40개 × `run_index` 0**이다. `run_index`는 0, 1, 2가 아니라 세 디렉터리 모두 0이다(FP-076: 이 가정은 이미 한 번 깨졌다). 실행 단위의 키는 `(result_dir 이름, task_id, run_index)`다. 한 디렉터리의 `grades.json`은 `task_id`를 키로 하는 dict라 task마다 한 행만 담는다.
- 관측 행 키 20개: `answer`·`approvals`·`cited_doc_ids`·`effect_bodies`·`effects`·`fault_injected`·`observed_ids`·`policy_violations`·`run_index`·`status`·`stop_reason`·`task_id`·`tool_calls`·`tool_results`·`trace_id`. 이 중 `tool_calls`는 `{name, ok}` 두 키뿐인 dict 목록이다.
- `grades.json[task_id]`: 키 `tcr`·`rules`·`used_auto_forbidden`. `tcr` = `{task_id, run_index, passed, reasons, tool_coverage}`.
- 호출 이름: 읽기 `search_docs`·`lookup_id`·`get_document`·`jira_get_ticket`·`run_sql`, 쓰기 `slack_send_message`·`jira_add_comment`·`jira_create_followup_ticket`. **관측 `tool_calls`는 `observation_calls(state, extract_tool_trace(captured))`다(`dev_m3.py:986~987`, `evalrun/tool_trace.py`). 읽기 항목 = 실제 실행된 읽기 호출(런타임 캡처)을 모델의 요청 순서에 맞춘 것이고 `ok` = 도구 결과가 오류가 아님이다. 쓰기 항목 = `proposal_calls(state)`, 즉 모델이 제출한 쓰기 초안이며 실행 전에 막힌 것도 포함한다. 쓰기 `ok` = 그래프 상태의 해당 tool 응답 메시지에 `error` 키가 없음이고 승인·거절·실제 발송 여부와 무관하다. 실제 부작용은 관측의 `effects`에 따로 기록된다. 공식 `grade_tcr`의 `tool_coverage`·`forbidden_tool_called`도 이 `tool_calls`를 쓴다**(FP-045: 읽기와 쓰기는 세는 대상이 다르다). 공식 N2 3개 디렉터리는 `dev_m3.py` 경로로 만들어졌다(manifest의 `tool_trace_mismatch`·`repeat_index`, 관측 행 키로 확인).
- trace(`traces/<trace_id>.jsonl`, `trace_id`는 관측 행과 `manifest.trace_ids[task_id]`에서 같다): 스팬 `kind`가 `tool_call`인 것 중 **읽기 도구만 도구 이름의 스팬**이고(`attributes`에 `success`·`error_code`·`tool.result`), 이름이 `tool_attempt`인 스팬(`success`·`error_code` 없음, 속성 키는 `service`·`soft_timeout_exceeded`)도 `kind`가 `tool_call`이다. 쓰기 호출에는 도구 이름 스팬이 없다. 수가 서로 맞지 않는다(N1a: 관측 호출 52, 도구 이름 스팬 40, `tool_attempt` 49, `kind == "tool_call"` 합계 89).
- 이 5개 디렉터리에서 `error_code`가 있는 스팬은 N2 2회·3회 각 1건(`not_found`, 읽기 호출 `get_document`)뿐이다.

**등록 문서에 쓸 열람 사실 공개(FP-043)**: 등록 작성자(조언 세션·구현 세션)는 형태 점검 중 위 표의 호출 합계와 `ok=false` 건수를 이미 열람했다. 이 값은 지표의 분자·분모와 직접 관련되지만 이 측정은 **합격선이 없는 보고용**이라 판정에 영향이 없다. 등록 문서에 이 사실을 한 문단으로 쓴다. 표의 개수 자체는 등록 문서에 쓰지 않는다(결과는 `make m8-toolcall`이 낸다).

### 2. `ok=false`의 의미 — 계약 테스트 (`tests/test_m8_toolcall_contract.py`)

위 `dev_m3.py`·`registry.py`·`graph.py`를 읽고 아래를 **가짜 런타임·합성 입력으로 직접 호출하는 테스트로 고정**한다(실제 서비스 접속 금지). 결과는 4번 문서의 "ok의 정의" 절에 그대로 옮긴다.

1. 도구가 오류 결과(`is_error`)를 돌려주면 관측의 `ok`는 false다(예: `jira_get_ticket` 없는 티켓 `not_found`).
2. 스키마 위반 인자는 `ToolRuntime.call`이 `invalid_argument` 오류 결과를 돌려주므로 `ok=false`로 기록된다(실제로 그런지 테스트가 확인한다. 다르면 다른 대로 문서에 쓴다).
3. `tools.call`이 예외를 던져도(타임아웃·서킷 등) `ok=false`로 기록되고 호출이 누락되지 않는다.
4. **그래프 단계에서 거절된 읽기**(인자가 JSON이 아니거나 신원 필드를 담은 경우 `invalid_argument`, 실행 불가 도구 `tool_not_available`; 런타임 캡처 없음)는 `tool_calls`에 **나타나지 않는다**. 같은 조건의 **쓰기 초안**은 tool 응답에 `error`가 있으면 `ok=false`로 **나타난다**(비대칭). 가짜 state와 합성 입력으로 `observation_calls`·`proposal_calls`를 직접 호출해 단언한다.
5. 쓰기 항목은 모델이 제출한 초안이며 `ok`는 tool 응답의 `error` 키 유무로만 정해진다. 실행·승인·거절·발송 여부와 무관하다(합성 state로 확인).
6. 요청과 짝이 맞지 않은 캡처 읽기(모델 요청 목록에 없는 읽기)는 목록 **끝에 붙는다**(진단용).

테스트는 위 6가지가 현재 코드의 동작임을 단언한다. 이 테스트의 실패는 등록 문서의 정의가 틀렸다는 뜻이다.

### 3. `tests/test_m8_freeze.py`

`tests/test_m7_freeze.py`와 같은 방식(`scripts/freeze_digest.py`를 `importlib`로 불러 `digest_for`를 쓰고, **`scripts/freeze_digest.py`는 수정하지 않는다**)으로 M8 사전 등록 문서의 sha256을 고정한다: `docs/results/hypotheses-m8-toolcall.md`(이 step이 만든 최종 본문), `hypotheses-m8-grader.md`, `hypotheses-m8-grader-addendum-1.md`~`-4.md`, `hypotheses-m8-citation.md`, `hypotheses-m8-hallucination.md`. 규칙: 키는 파일 경로 문자열의 `dict`이고, 모든 키가 추적 중인 파일이며, 값이 현재 digest와 같고, `freeze_digest.FREEZE_PATHS`와 겹치지 않는다(겹치면 실패). `EXPECTED`에 파일을 더하는 유일한 방법은 새 사전 등록 파일을 이 `dict`에 키로 추가하는 것이다. 이 step 이후 `hypotheses-m8-regression.md`(R-0)가 한 키를 더한다.

### 4. 등록 문서 `docs/results/hypotheses-m8-toolcall.md`

형식은 `hypotheses-m8-citation.md`를 따른다. 아래 절을 이 순서로 쓴다.

1. **표기**: 첫 줄 아래에 `M8 추가 측정 — 공식 M6 수치 아님`, `scope: "m8"`. 공식 TCR·`tool_coverage`와 합산하거나 같은 표에 쓰지 않는다. 지표 이름: 필수 도구 호출률 `required_tool_call_rate`, 금지 호출 건수 `forbidden_call_count`, 호출 성공률 `call_ok_rate`(결정 10번: "도구 선택 정확도"라고 부르지 않는다). 기존 `tool_coverage`는 진단값이고 M8 지표가 아니다.
2. **열람 사실 공개**(1번 끝 문단).
3. **측정 단위·대상**: 키 `(result_dir, task_id, run_index)`. 공식 그룹: 위 N2 3개 디렉터리(120 실행, 디렉터리 ID와 `repeat_index` 1·2·3을 적는다). 참고 그룹: N1a·N1b. 그룹은 따로 보고하고 합산하지 않는다.
4. **세 지표의 정의**(분자·분모를 문장으로):
   - `required_tool_call_rate`: 분모 = `required_tools_any`가 **비어 있지 않은** 실행, 분자 = 모든 그룹에서 성공(`ok=true`) 호출이 하나 이상. 그룹이 비면 분모에서 빼고 건수 `excluded_empty_groups`를 보고한다(`all([])` 공허 참 방지, FP-069). 분모 0이면 실패 코드 `m8_toolcall_denominator_zero`.
   - `forbidden_call_count`: 실행 안의 금지 도구 호출 **횟수**와 그런 실행 수(`forbidden_call_runs`)를 둘 다. 분모 = 전체 실행. 호출 성공 여부와 무관하게 이름으로 센다(`grade_tcr`와 같다). 0이 좋은 지표이므로 짝으로 `required_tool_call_rate`와 공식 TCR을 함께 보고한다(FP-040: 아무것도 안 해서 0이 되는 경우를 가린다).
   - `call_ok_rate`: 분자 = `ok` 호출 수, 분모 = 관측의 도구 호출 수(정의는 그대로). 읽기 = 실행된 읽기 중 오류 없음, 쓰기 = 제출한 초안 중 그래프·정책 단계에서 오류 응답을 받지 않음이다. **읽기·쓰기를 반드시 나눠 보고하고** 합계는 참고로만 쓴다(R-0 기준 4는 합계를 판정에 쓰지만 읽기·쓰기 값을 함께 보고한다). 분모 0이면 `m8_toolcall_denominator_zero`.
5. **ok의 정의**: 2번 계약 테스트 결과를 그대로 쓴다. `call_ok_rate`는 "호출 성공률"이며 스키마 위반 비율이 아니다. **한계로 쓴다**: 그래프 단계에서 거절된 읽기는 분모에 없고 거절된 쓰기 초안은 분모에 있다(비대칭). 쓰기 `ok`는 실행 성공이 아니다. 쓰기 항목은 실행된 부작용이 아니라 제출된 초안이며 실제 부작용은 `effects`에 있다.
6. **`tool_coverage` 일치 검증**: 각 실행에서 `required_met`(그룹이 비면 참)이 `grades.json`의 `tcr.tool_coverage`와 같다. 하나라도 다르면 코드 `m8_toolcall_grade_mismatch`로 중단한다.
7. **오류 코드 분해**: 읽기 호출만 trace의 도구 이름 스팬(`tool_attempt` 제외)의 `error_code`와 연결한다. 한 trace 안에서 관측의 읽기 호출 `(name, ok)` 다중집합이 스팬의 `(name, success)` 다중집합과 같을 때만 연결로 인정하고, 다르면 그 실행의 읽기 호출을 `unlinked_read`로 센다. 쓰기 항목은 제출된 초안이고 도구 이름 스팬이 없으므로 항상 `unlinked_write`로 세며 이는 결함이 아니다. trace 파일이 없으면 `unlinked_trace_missing`. 조용히 빼지 않고 건수를 보고한다.
8. **목표 없음**: 합격선을 두지 않는다. 결과가 어떻든 판정하지 않고 보고한다. 항목 5의 상대 한도가 이 지표를 쓰지만 그것은 R-0의 등록이다.
9. **입력·출력 규칙**: 입력 파일 `observations.jsonl`·`grades.json`·`manifest.json`의 sha256과 `traces/`의 (파일 이름, sha256) 목록의 sha256을 출력 `report.json`에 기록한다. 입력은 읽기 전용. 출력은 `data/interim/m8-metrics/toolcall/`(gitignore, FP-067: `data/results/` 아래 새 파일은 러너의 `dirty_worktree` 검사에 걸린다). 단일 진입점 `python -m govops.metrics.cli toolcall`과 `make m8-toolcall`.
10. **고정 코드 목록**: 중단 코드(원인마다 다른 이름) `m8_toolcall_registration_not_committed`, `m8_toolcall_input_missing`, `m8_toolcall_input_invalid`, `m8_toolcall_unknown_task`, `m8_toolcall_duplicate_run`, `m8_toolcall_grade_mismatch`, `m8_toolcall_denominator_zero`, `m8_toolcall_out_overlaps_input`, `m8_toolcall_out_exists`, `m8_toolcall_hash_mismatch`; 보고 카운터 `unlinked_read`·`unlinked_write`·`unlinked_trace_missing`·`excluded_empty_groups`. 이 목록이 코드 이름의 단일 출처다(T-1·T-2의 테스트가 문서와 코드 일치를 검사한다).
11. **결과 표현 한계**: "공식 N2 120 실행에서 필수 호출률 X/Y, 금지 호출 Z건"처럼 분모를 함께 쓴다. "도구 선택 정확도 N%"로 일반화하지 않는다. 0건은 관측값이지 증명이 아니다. 에이전트·프롬프트·Guardrail은 측정을 위해 바꾸지 않았다.
12. **사용자 실행**(블록당 한 명령): `make m8-toolcall TOOLCALL_ARGS="…"`의 인자 예(공식 3개 디렉터리, 참고 2개 디렉터리).

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

- 지표 정의의 분모를 데이터를 본 뒤의 사정에 맞춰 바꾸지 마라. 이유: 결과 열람 전 등록이 이 문서의 목적이다(FP-043). 이 문서는 커밋 뒤 `tests/test_m8_freeze.py`로 동결된다.
- `run_index`가 0, 1, 2로 이어진다고 가정해 문서·테스트를 쓰지 마라. 이유: 실측은 세 디렉터리 모두 0이다(FP-076).
- `tool_coverage`·`rules.unapproved_effect_count`와 M8 지표를 같은 이름·같은 표로 합치지 마라. 이유: 세는 대상이 다르다(FP-045). `rules.unapproved_effect_count`는 내가 확인한 네 디렉터리(N1a, N2 3회)에서 각각 task 하나가 1이지만 `tcr.reasons`의 `unapproved_effect`와 다른 기록이다.
- 계약 테스트에서 실제 도구·LiteLLM·mock 컨테이너에 접속하지 마라. 이유: AC는 네트워크 없이 통과해야 한다. `tests/test_tool_registry.py`의 가짜 런타임 방식을 쓴다.
- `scripts/freeze_digest.py`와 `tests/test_m7_freeze.py`를 수정하지 마라. 이유: M7 동결 digest는 공식 구성 불변 증거다(설계 1-3절).

## 자기 점검표 (작성자 기입. 문제 신호: 첫째 질문 "예", 둘째 질문 "아니오", 셋째 질문 "예")

- 수정 파일 목록 밖의 변경이 필요한가: 아니오.
- AC를 이 step 안에서 달성할 수 있는가: 예.
- 다른 step·등록 문서와 모순되는가: 아니오. 설계 초안의 출력 위치 `data/results/m8/…`는 FP-067 때문에 `data/interim/m8-metrics/…`로 바꿨다(이 문서가 단일 출처이고 설계 초안 1-4절과 다르다. 보고에 포함).
