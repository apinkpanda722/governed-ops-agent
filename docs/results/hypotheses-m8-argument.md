# M8 도구 인자 근거율 사전 등록 (`hypotheses-m8-argument.md`)

**M8 추가 측정 — 공식 M6 수치 아님** · `scope: "m8"`

상태: **등록 커밋 대기**. 이 파일은 측정 코드(`govops.grounding.argument`)보다 먼저 커밋하고 커밋 뒤에는 수정하지 않는다(FP-043). `tests/test_m8_freeze.py`가 이 문서의 digest를 고정하고, CLI는 등록 커밋이 없거나 이 파일이 바뀌어 있으면 `m8_args_registration_not_committed`로 멈춘다. 합격선은 없다(보고용).

## 1. 표기와 목적

지표 이름은 `argument_id_grounded_rate`(인자 ID 근거율)와 `ungrounded_call_rate`(근거 없는 ID를 담은 호출 비율)다. 공식 TCR·`tool_coverage`·① 도구 호출 지표(`required_tool_call_rate`·`forbidden_call_count`·`call_ok_rate`)와 합산하거나 같은 표에 쓰지 않는다. "도구 선택 정확도"나 "인자 정확도"라고 부르지 않는다.

목적: 모델이 도구 인자에 넣은 식별자(문서 ID·장애 ID·티켓 키)가 **그 호출 시점에 이미 모델이 본 근거에 있는지**를 결정론으로 잰다. 평가 명세에는 정답 인자가 없으므로 Ragas ToolCallAccuracy 식의 정답 인자 대조는 하지 않는다.

① 결과에서 확인된 사실: 쓰기 초안은 정책·범위 검사에서 막혀도 관측 `tool_calls`에서 `ok=true`다(공식 N2-1 `T3-05`). 이 지표는 `ok`와 무관하게 **제출된 모든 호출의 인자**를 본다.

## 2. 열람 사실 공개

등록 작성자(어드바이저 세션)는 live DB 점검으로 **thread 개수와 `assistant_tool_calls` 개수만** 보았다(공식 N2 3개 + N1a의 140행이 모두 thread와 연결됨, assistant tool_calls 318). **인자 값은 보지 않았다.** 응답·발송 본문과 근거 본문도 열람하지 않았다. 이 등록에는 그 개수를 근거로 한 기대치를 싣지 않는다.

## 3. 데이터와 ADR-015 예외 재등록

- 인자는 관측 `tool_calls`(이름·`ok`만), trace, 감사 체인(`args_sha256` 해시만)에 없고 **Postgres checkpointer의 assistant 메시지 `tool_calls[].function.arguments`에만** 있다.
- 사용자가 2026-10-20에 Postgres 전체 덤프 `data/interim/m8-evidence/govops-2026-10-20.dump`를 만들었다. 이 덤프를 **임시 DB `govops_m8_args`로 복원**해 **읽기 전용**으로 읽는다(`--dbname govops_m8_args`). 기존 `govops` DB에는 쓰지 않는다. 연결은 `govops.grounding.db.open_readonly_saver`(`default_transaction_read_only=on`, 스키마 생성 없음)를 쓴다.
- **ADR-015 예외 재등록**: 덤프와 복원 DB에는 checkpointer 원문이 있다. 이 예외의 목적은 **이 측정에 한정**한다. 삭제는 **결과 PR 머지 직후 사용자가 실행**한다: 임시 DB `dropdb`와 덤프 파일 삭제. 결과 문서에 삭제 실행 여부를 기록한다. 대상 데이터는 합성(`syn-*` 문서, 합성 티켓·사용자)이고 토큰은 state·checkpoint에 없다(AGENTS.md CRITICAL).
- 인자 원문은 CLI 출력·로그·보고서·테스트 기대값에 쓰지 않는다. 출력에는 개수·비율·ID 개수와 도구 이름만 남긴다.

## 4. 측정 단위·대상

실행의 고유 키는 `(result_dir, task_id, run_index)`다(세 공식 디렉터리의 `run_index`는 모두 0이다, FP-076). 실행과 thread의 연결은 `govops.grounding.probe.link_results`를 **그대로 재사용**한다(관측 → `attempts/<task>-<n>/attempt.json` → 감사 `task_id` / trace ID → `thread_id`).

| 그룹 | `data/results/` 아래 디렉터리 |
|---|---|
| 공식 N2 1회 | `test/09fbcb2be83f47669800da400a401e55` |
| 공식 N2 2회 | `test/b330337d70fa4cf9a6c25773fb9b1359` |
| 공식 N2 3회 | `test/bf38e57a74cc49eda2f2be522f14e063` |
| 참고 N1a(v1) | `dev/6b3ec0b1ec92402db7ebefb2d4cfb1b4` |
| 참고 N1b(v1+v2) | `dev/6dba4af230d74d8b93a64bf6cbfa0b9a` |

공식 N2 3개(120 실행)는 하나의 공식 그룹이고, 참고 N1a·N1b는 각각 별도 그룹이다. **그룹끼리 합산하지 않는다.**

한 행에 연결된 thread가 여럿이면 `thread_id` 오름차순으로 **assistant 메시지에 `tool_calls`가 하나 이상 있는 첫 thread**를 쓴다(근거 재구성의 "도구가 있는 첫 후보" 규칙과 같다). 둘 이상의 thread가 호출을 가지면 행 카운터 `multi_thread_rows`로 센다.

## 5. 정의

1. **인자에서 ID 뽑기**: 호출의 `function.arguments`를 JSON으로 파싱한 뒤 값 전체를 재귀로 훑어 **모든 문자열 값**에서 `govops.grounding.deliverable.extract_citations`로 ID를 뽑는다(인용 지표와 같은 패턴: `INC-\d{4}-\d{3}`, `syn-[a-z]+-\d{3}`, `[A-Z]{2,5}-\d{4}` 티켓 키). 한 호출 안의 같은 ID는 한 번만 센다(첫 등장 순서). 키 이름과 숫자·불리언 값은 보지 않는다. 자유 텍스트 값(검색어·메시지 본문) 안의 ID도 뽑히지만 그 텍스트의 의미는 평가하지 않는다.
2. **호출 시점 이전의 근거**: (가) 해당 행의 사용자 요청 문장 `spec.request`, (나) 같은 thread의 메시지 목록에서 **그 호출이 들어 있는 assistant 메시지보다 앞에 있는** `role:"tool"` 메시지 내용 전부. 두 곳의 문자열에서 같은 `extract_citations`로 뽑은 ID 집합을 근거 집합이라 한다. 같은 assistant 메시지의 다른 호출 결과나 이후 메시지는 근거가 아니다.
3. **grounded / ungrounded**: 인자 ID가 근거 집합에 있으면 grounded, 없으면 ungrounded.
4. **호출의 읽기·쓰기 구분**: 도구 이름이 `slack_send_message`·`jira_add_comment`·`jira_create_followup_ticket`이면 쓰기, 나머지는 읽기다. 쓰기의 관측 `ok`는 같은 행의 관측 `tool_calls` 중 쓰기 항목을 **제출 순서대로** 짝지어(이름이 같을 때만) 가져온다. 짝이 맞지 않으면 그 호출은 `ok` 미상(`ok_unpaired`)으로 센다.

## 6. 보고 지표 (합격선 없음)

- `argument_id_grounded_rate` = grounded ID 수 / 인자 ID 전체 수 (호출별 고유 ID의 합).
- `ungrounded_call_rate` = ungrounded ID가 하나라도 있는 호출 수 / ID를 하나라도 담은 호출 수.
- 두 지표 모두 **읽기·쓰기를 나눠** 보고하고 합계는 참고로만 쓴다. 쓰기는 관측 `ok` 값별(`true`·`false`·미상)로도 나눠 보고한다.
- 비율은 정수 분자·분모 쌍으로 보고한다. 분모 0인 하위 집합(예: `ok=false` 쓰기)은 `0/0`, 비율 `null`로 쓰고 중단하지 않는다. 한 **그룹 전체**의 ID 수가 0이면 `m8_args_denominator_zero`로 중단한다.
- 함께 보고할 건수(그룹별): `calls_without_id`(ID 없는 호출), `arguments_parse_failed`(인자 JSON 파싱 실패 호출; 이 호출은 ID 0개로 세지 않고 별도 건수), `observed_calls`·`assistant_calls`(관측 `tool_calls` 수와 checkpointer assistant `tool_calls` 수)와 둘의 차이, 행 카운터 `rows`·`unlinked_rows`(연결된 thread 없음)·`checkpoint_missing`(thread는 있으나 체크포인트 없음)·`rows_without_calls`·`multi_thread_rows`. 그래프에서 거절된 읽기는 관측에 없으므로 차이가 생길 수 있다. 조용히 빼지 않고 건수를 보고한다.

## 7. 한계

- **정답 인자 대조가 아니다.** 근거에 있는 ID를 잘못 골라도 grounded로 센다. 근거 밖 ID(ungrounded)는 환각, 사용자 요청 문장 밖의 추론, 이전 turn의 근거, 잘린 tool 결과(`…(잘림)`) 때문일 수 있어 원인을 구별하지 않는다.
- 자유 텍스트 인자(검색어·메시지 본문)의 의미는 평가하지 않는다. ID 패턴에 맞지 않는 식별자는 보지 않는다.
- 근거는 요청 문장과 앞선 tool 메시지뿐이다. 다중 turn 대화의 이전 turn 근거는 없다(평가 task는 단일 turn).
- 관측 쓰기 `ok`는 제출 초안의 tool 응답에 `error` 키가 없다는 뜻이며 실행 성공이 아니다. 정책·범위 검사에서 막힌 초안도 `ok=true`다(① 결과 문서 §2-1).
- 결과 표현: "공식 N2 120 실행의 인자 ID X/Y가 호출 이전 근거에 있다"처럼 분모를 함께 쓴다. 0건 ungrounded는 관측값이지 증명이 아니다. 에이전트·프롬프트·Guardrail은 측정을 위해 바꾸지 않았다.

## 8. 고정 코드 목록 (단일 출처)

중단 코드(원인마다 다른 이름): `m8_args_registration_not_committed`, `m8_args_input_missing`, `m8_args_input_invalid`, `m8_args_unknown_task`, `m8_args_duplicate_run`, `m8_args_out_overlaps_input`, `m8_args_out_exists`, `m8_args_url_env_unset`, `m8_args_db_connect_failed`, `m8_args_thread_read_failed`, `m8_args_checkpoint_shape`, `m8_args_denominator_zero`.

보고 카운터: `calls_without_id`, `arguments_parse_failed`, `ok_unpaired`, `unlinked_rows`, `checkpoint_missing`, `rows_without_calls`, `multi_thread_rows`.

이 목록이 코드 이름의 단일 출처이며 CLI 테스트가 문서와 코드의 일치를 검사한다.

## 9. 입력·출력 규칙

- 입력은 읽기 전용이다. `report.json`에 `registration_commit`, 평가 명세와 `observations.jsonl`의 sha256, `attempts/` 아래 `attempt.json`·`audit/chain-*.jsonl`의 (경로, sha256) 목록 sha256, 읽은 thread ID 목록의 sha256을 기록한다.
- 출력은 `data/interim/m8-args/`(gitignore, `data/results/` 아래 새 파일은 러너의 `dirty_worktree` 검사에 걸린다, FP-067)이며 새 경로여야 한다(`m8_args_out_exists`). 출력 파일: `report.json`(집계), `rows.jsonl`(호출 단위: `result_dir`·`task_id`·`run_index`·호출 순번·도구 이름·읽기/쓰기·`ok`·ID 개수·grounded 개수; **인자 원문 없음**), `report.md`(사람이 보는 표). 최상위 집계는 `rows.jsonl`과 같은 입력에서 계산하며 일치 테스트가 검사한다.
- 환경 변수 `GOVOPS_DATABASE_URL`(`--url-env`로 바꿀 수 있음)이 없거나 비어 있으면 `m8_args_url_env_unset`으로 끝난다. 단일 진입점은 `python -m govops.grounding.argument`와 `make m8-args`다.

## 10. 사용자 실행 (블록당 명령 하나)

덤프를 임시 DB `govops_m8_args`로 복원한 뒤 측정한다. 복원은 임시 DB에만 한다.

```bash
docker compose --env-file .env -f infra/docker-compose.yml exec -T postgres sh -c 'createdb -U "$POSTGRES_USER" govops_m8_args'
```

```bash
docker compose --env-file .env -f infra/docker-compose.yml exec -T postgres sh -c 'pg_restore -U "$POSTGRES_USER" --no-owner -d govops_m8_args' < data/interim/m8-evidence/govops-2026-10-20.dump
```

```bash
make m8-args ARGUMENT_ARGS="--official data/results/test/09fbcb2be83f47669800da400a401e55 data/results/test/b330337d70fa4cf9a6c25773fb9b1359 data/results/test/bf38e57a74cc49eda2f2be522f14e063 --reference data/results/dev/6b3ec0b1ec92402db7ebefb2d4cfb1b4 data/results/dev/6dba4af230d74d8b93a64bf6cbfa0b9a --dbname govops_m8_args"
```

```bash
cat data/interim/m8-args/report.md
```

결과 PR 머지 직후 임시 DB와 덤프를 삭제한다.

```bash
docker compose --env-file .env -f infra/docker-compose.yml exec -T postgres sh -c 'dropdb -U "$POSTGRES_USER" govops_m8_args'
```

덤프 파일(`data/interim/m8-evidence/govops-2026-10-20.dump`)은 사용자가 직접 삭제한다.
