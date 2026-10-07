---
name: failure-patterns
description: 이 프로젝트(governed-ops-agent)에서 실제로 겪은 실패 패턴과 재발 방지 규칙. /harness로 step을 설계할 때와 /review로 변경을 검토할 때 반드시 먼저 읽는다. 부작용 실행·재시도·멱등성, 승인 흐름, 감사 로그, 외부 서비스(LiteLLM·mock·A MCP) 계약, 비용 원장, 환경 변수·설정, 테스트의 무작위·순서·CI 환경 의존, 평가 판정 진입점·사전 등록, phase 설계·step 파일 작성·실행 계획을 다루는 작업이면 해당 항목을 step 금지사항과 테스트로 옮긴다.
---

# 실패 패턴

같은 실패를 두 번 겪지 않기 위한 기록이다. 항목마다 **강제 수단**이 있다. 강제 수단이 테스트면 그 테스트가 1차 방어선이고, 이 문서는 새 코드에 같은 패턴을 옮길 때 참고한다. 항목 번호(FP-NNN)는 이 저장소의 번호이며 프로젝트 A의 같은 이름 스킬과 번호가 다르다(A에서 가져온 항목은 "A에서 이식" 절에 `A-FP-NNN`으로 적는다).

## 사용법

- **/harness (step 설계)**: 작업과 관련된 항목을 골라 그 step의 `## 금지사항` 또는 `## 작업`에 **이유와 FP 번호를 함께** 옮겨 적는다. Codex는 이 파일을 읽지 않고 step 파일만 읽는다. 수량·범위 기준은 무엇을 세는지까지 적는다(A-FP-043).
- **/review**: 변경된 코드에 아래 패턴이 다시 나타나는지 확인한다. 이번 phase에서 새로 겪은 실패(수정 커밋이 있었던 것)는 이 문서에 항목으로 추가하는 초안을 제시한다.
- **승격**: 같은 패턴이 두 번째로 나오거나 기계적으로 검사할 수 있으면 테스트로 만들고 강제 수단에 테스트 이름을 적는다.

## 항목 형식

`### FP-NNN 제목` / 증상 / 원인 / 규칙 / 강제 수단 / 출처(phase·커밋·실측)

---

## A. 부작용 실행·멱등성·승인

### FP-001 실행이 반영된 뒤의 오류를 "보내지 않았음이 확실함"으로 분류
- **증상**: 발송이 2xx로 반영됐는데 응답 본문이 깨지면 `rest.py`가 `ToolHttpError(200, "invalid_response")`를 던지고, 분류기가 2xx 오류와 알 수 없는 예외를 `DEFINITIVE`로 두었다. 실행기가 표식 조회 없이 `FAILED`로 끝내면 이미 반영된 발송을 "실패"로 기록하거나 재발송 경로에 들어갈 수 있다.
- **원인**: `DEFINITIVE`를 "그 밖의 모든 것"의 기본값으로 두었다. 확정 실패는 "서버가 처리하지 않았음이 확실한 경우"로 좁게 정의해야 한다.
- **규칙**: `FAILED`(보내지 않았음이 확실)는 4xx 확정 거절, 서킷 열림으로 호출하지 않음, 연결 수립 실패·429만 반복, 첫 발송 전 감사 실패로 한정한다. 2xx인 오류, 분류할 수 없는 예외, 마지막 시도가 타임아웃·5xx·전송 오류로 끝난 경우는 `UNCERTAIN`이고 표식 조회 또는 `reconcile_effect`로만 확정한다.
- **강제 수단**: `tests/test_resilience_effects.py`·`tests/test_tool_resilience.py`(2xx 오류·미분류 예외 → `UNCERTAIN`, 쓰기에서 2xx + 깨진 본문 → 표식 조회로 `SUCCEEDED`·재발송 0), `tests/test_effects_matrix.py`
- **출처**: m3-effects 검토(step 4 지침 보강)

### FP-002 성립할 수 없는 장애 시나리오 기대값
- **증상**: "F2 연속 장애 → 서킷 열림"을 매트릭스 기대값으로 적었는데, 표식 조회 GET의 성공이 서킷의 연속 실패 횟수를 0으로 되돌리고 효과당 발송 시도도 5회 미만(최대 3회)이라 서킷이 열릴 수 없었다.
- **원인**: 시나리오 기대값을 구현 규칙(서킷 임계값, 시도 횟수, 어떤 호출이 실패 카운터에 드는지)에서 도출하지 않고 시나리오 이름에서 추측했다.
- **규칙**: 시나리오의 기대 결과는 해당 규칙의 값(임계값·시도 횟수·카운터 갱신 조건)으로 **계산**해서 적는다. 서킷처럼 여러 호출의 합으로 정해지는 동작은 따로 시나리오(발송·조회 모두 실패, 여러 효과를 이어 실행)로 분리한다. 단언할 수 없는 것은 단언하지 않는다("서킷은 단언하지 않는다"를 명시).
- **강제 수단**: `tests/test_effects_matrix.py`(F2 연속은 발송 시도 소진·`UNCERTAIN`·누락 1, `test_circuit_no_requests_after_open`은 별도 시나리오)
- **출처**: m3-effects 검토(step 4 지침 수정)

### FP-003 수정본이 막힌 뒤 결정 영수증 때문에 task가 멈춤
- **증상**: 승인 API가 첫 결정 때 `approval_receipt`(원래 해시, `edit_approve`)를 저장했다. 수정본이 결정론 검사에 막혀 `awaiting_approval`로 돌아온 뒤에는 모든 요청이 이 영수증과 비교되어, 새 해시로 거부·재수정·승인하면 409가 났고 만료까지 막혔다.
- **원인**: 멱등 응답용 영수증을 "결정을 받았을 때"가 아니라 "결정이 task를 종결하거나 실행으로 넘겼을 때"로 한정하지 않았다. 상태가 다시 대기로 돌아가는 분기를 테스트하지 않았다.
- **규칙**: 영수증은 종결 또는 실행 전환 때만 저장하고, 결과가 다시 `awaiting_approval`이면 저장하지 않는다(있으면 지운다). 대기로 되돌아가는 분기(수정본 차단)마다 "그 뒤의 거부·재수정·승인·만료"를 테스트한다. 되돌아간 초안은 항상 검사를 통과한 원래 초안과 원래 해시여야 하고 만료 시각은 연장하지 않는다.
- **강제 수단**: `tests/test_api_tasks.py`(`test_blocked_edit_restores_draft_and_allows_next_decision` 등 (a)~(e))
- **출처**: m3-approval 검토(step 7 결함 수정)

### FP-004 선점에 실패한 호출이 다른 실행의 outbox 행을 갱신
- **증상**: 이미 `SUCCEEDED`인 키로 재실행(`claimed=false`)했을 때 결과 감사 기록이 실패하면 원래 실행의 outbox 행에 `audit_gap=True`가 찍혔다.
- **원인**: 결과 기록 실패 처리를 선점 성공·실패 경로가 공유했다.
- **규칙**: 선점하지 못한 호출은 outbox 행을 **읽기 전용**으로 다룬다. 그 호출의 감사 실패는 반환값(`EffectResult.audit_gap`)에만 반영한다.
- **강제 수단**: `tests/test_effects_matrix.py::test_claim_reexecution`(선점 실패 + 결과 감사 실패 → 원래 행의 `audit_gap`·상태 불변, `EffectResult.audit_gap`만 True)
- **출처**: m3-effects 검토 → m3-approval step 0

### FP-005 주입한 장애가 시험하려는 검사 경로를 타지 않음
- **증상**: F10(조회 반영 지연)을 단독 시나리오로 두면 응답은 정상이라 표식 조회가 일어나지 않는다. 중복 0이 "표식 조회가 잘 동작해서"가 아니라 아무 일도 안 일어나서 저절로 나온다.
- **원인**: 장애 주입을 "그 장애가 발생함"까지만 정의하고, 그 장애가 방어 로직(표식 조회 → 대기 → 재조회)을 실제로 실행시키는지는 확인하지 않았다.
- **규칙**: 장애 시나리오는 시험하려는 경로를 **반드시 타도록 결합**하고 그 경로가 실제로 실행됐다는 증거(조회 호출 수 > 0, 대기 발생)를 단언한다. F10(조회 반영 지연)은 항상 F5(처리 후 응답 유실)와 결합한다. 사전 등록에도 결합으로 적는다.
- **강제 수단**: `tests/test_effects_matrix.py`(F5 + 반영 지연 짧게 → 2단계 조회 발견·중복 0, 길게 → 중복 1 `known_limit`), THREAT_MODEL F10 행(F5와 결합)
- **출처**: m3-effects 설계 검토(step 4 지침, `da3e560`), 문서 반영 `a0e4f30`

## B. 입력 검증·개인정보 판정

### FP-010 식별자·해시에 개인정보 정규식을 돌려 정상 값이 무작위로 거부됨
- **증상**: 감사 필드 검증이 해시에 PII 정규식을 돌려 무작위 sha256 hex 약 1%가 카드번호 규칙(13자리 이상 연속 숫자 + Luhn)에 걸렸다. 다시 `task_id`·`actor_sub`에 같은 검사를 돌려 `uuid4().hex` 무작위 10만 개 중 0.453%가 전화번호 규칙에 걸렸다. 걸린 task는 초안 감사 기록이 실패해 `audit_write_failed`로 끝났다(dev·장애 주입·서비스에서 무작위 실패).
- **원인**: "원문 금지"를 "모든 문자열에 PII 검사"로 구현했다. 형식이 정해진 식별자·해시는 PII 검사가 아니라 **형식 검증**의 대상이다.
- **규칙**: 해시(`^[0-9a-f]{64}$`)·task ID(`^[0-9a-f]{32}$`)·사용자 라벨(`^demo-[a-z-]+-g[0-9]+$`)·mock ID·코드 상수는 형식으로 검증하고 PII 정규식을 돌리지 않는다. PII 검사는 형식 밖 문자열에만 쓰고, 형식 밖 값은 허용 목록 밖이므로 항상 거부한다. 식별자를 받는 검증 함수는 무작위 입력 대량 테스트로 검증한다.
- **강제 수단**: `tests/test_audit.py`(`test_hash_and_mock_ids_skip_pii`: 해시·mock ID에 PII 검사를 돌리지 않음, `test_random_uuid_identifiers`: 시드 고정 무작위 `uuid4().hex` 10,000개 통과, `test_invalid_identifiers`: 형식 밖·PII 섞인 식별자 거부)
- **재발**: trace 마스킹 경로에서 같은 결함이 다시 나왔다. `gateway/client.py`가 `secrets.token_hex(8)`을 span 속성 `generation_id`로 남기고 `obs/mask.py`가 속성 문자열 전체에 `find_pii`를 돌려 무작위 숫자 연속이 전화번호 규칙에 걸리면 `cf[PII:mobile]bed2`처럼 가려졌다(`tests/test_gateway_client.py::test_generation_id_is_short_unique_and_recorded_on_the_span`가 무작위로 실패). trace 연결이 끊기고 CI가 무작위로 실패한다. 식별자 속성 한 군데를 고쳤는데 같은 규칙이 다른 경로(감사 로그 → trace 마스킹)에 남아 있었다.
- **강제 수단(추가)**: trace 마스킹 경로 `obs/mask.py`: 키와 형식이 모두 맞는 식별자 속성(`generation_id` `^[0-9a-f]{16}$`·`trace_id`·`task_id` `^[0-9a-f]{32}$`·해시 `^[0-9a-f]{64}$`·등록된 `deployment_id`)만 PII 검사를 건너뛴다. 형식 밖 값은 검사한다. 확정된 테스트(m3-faults step 8, `27fc2cd`): `tests/test_obs_mask.py::test_random_identifier_attributes`(키별 시드 고정 무작위 10,000개가 가려지지 않음), `test_identifier_invalid_format_is_masked`(같은 키라도 형식 밖 값은 가려짐), `test_identifier_requires_known_key_and_string`, `test_hash_identifier_format`, `test_deployment_identifier_registry`, `test_identifier_secrets_are_still_scrubbed`, `test_identifier_survives_trace_jsonl_sink`(실제 sink 경로). 식별자를 받는 **모든** 검증·마스킹 경로(감사 로그·trace 마스킹)를 점검한다.
- **출처**: m3-effects(해시), m3-gateway 검토(`task_id`·`actor_sub`), m3-faults batch 0-3 Stop hook 실행(trace 마스킹)

### FP-011 무작위로 실패하는 테스트를 입력 고정으로 덮어 실제 결함을 가림
- **증상**: `uuid4`를 고정 ID로 `monkeypatch`하거나 테스트의 ID를 고정값으로 바꾸자 간헐적 실패가 사라졌다. 실제로는 FP-010의 결함(무작위 ID가 PII 규칙에 걸림)이었고 서비스에서는 계속 무작위로 실패했을 것이다.
- **원인**: 간헐적 실패를 테스트 결함으로 가정하고 입력을 고정했다.
- **규칙**: 무작위 입력에서만 나타나는 실패는 **실제 결함을 먼저 의심**한다. 코드를 고치고 고정을 되돌린다. `uuid`·난수·시간을 `monkeypatch`로 고정하는 테스트는 **사유 주석**이 있어야 하고, 같은 입력의 **무작위 대량 테스트**(시드 고정, 건수 명시)를 함께 둔다.
- **강제 수단**: /review 체크리스트 7번(`monkeypatch.*uuid`·고정 ID 변경 확인), `tests/test_audit.py::test_random_uuid_identifiers`
- **출처**: m3-gateway 검토(`4b53fb2`·`023a86d`의 고정 변경을 되돌림)

### FP-012 동시 실행 결과의 순서를 단언한 테스트
- **증상**: 동시 승인 2건의 응답을 `[202, 200]` 순서로 단언했다. 로컬에서는 6번 연속 통과했지만 CI에서 `[200, 202]`가 나와 실패했다. 제품 동작(하나는 수락 202, 다른 하나는 같은 결정의 멱등 응답 200, 효과 1)은 맞았다.
- **원인**: `asyncio.gather` 안의 요청들이 어느 쪽이 잠금을 먼저 얻는지는 스케줄링에 달려 있다. 테스트가 처리 순서까지 계약으로 단언했다.
- **규칙**: 동시 실행의 단언은 **결과의 집합·개수·불변식**(상태 코드 집합, 효과 수, 응답 본문 동일성)으로 한다. 순서에 의존하는 단언을 쓰려면 순서를 보장하는 장치를 코드가 제공하는지 확인한다. 간헐적 실패가 나오면 FP-011에 따라 제품 결함 여부를 먼저 확인하고, 제품이 맞으면 단언을 고친다(입력 고정으로 덮지 않는다).
- **강제 수단**: `tests/test_api_tasks.py`(`sorted(status_codes) == [200, 202]`), /review 체크리스트 7번
- **출처**: m3-gateway-fix PR CI 실패(`d16c4bf`)

## C. 외부 서비스 계약·가짜 응답

### FP-020 가짜 응답으로만 검증한 계약
- **증상**: `gateway/client.py`가 응답 본문 `model`을 제공자 모델 ID로 해석했고, 단위 테스트는 제공자 ID가 든 가짜 응답만 써서 통과했다. 실제 LiteLLM 프록시에서는 주 모델 응답의 본문 `model`이 별칭(`agent-primary`)이어서 `smoke-fallback` normal 단계가 `model_mismatch`로 실패했고, M2 dev의 `models_seen`도 별칭이 기록됐다.
- **실측으로 확인된 사실**: LiteLLM은 본문 `model`을 **일관되게 채우지 않는다**. 주 모델 응답의 본문은 별칭이었고, fallback 응답의 본문은 별칭이 아니라 제공자 ID였다. 응답 모델은 헤더 `x-litellm-model-id`(배포 ID)로 정해야 한다. 수정 후 `smoke-fallback`에서 normal은 `model_id=openai-gpt-6-luna`, `down_alias` fallback은 `model_id=anthropic-claude-sonnet-5-5`·`attempted_fallbacks=1`로 확인됐다.
- **원인**: 외부 서비스 응답 형태를 문서·추측으로 정하고 가짜 응답을 그 추측대로 만들었다. 테스트가 같은 추측을 검증하므로 틀려도 통과한다.
- **규칙**: 외부 서비스(LiteLLM·A MCP·mock이 흉내 내는 실제 API) 응답을 해석하는 코드는 **실제 응답 1건으로 필드·헤더를 확인한 뒤** 그 형태(본문이 일관되지 않을 수 있음 포함)를 픽스처로 쓴다. 실제 값을 볼 수 있는 진단 출력(비밀이 아닌 모델명·배포 ID·헤더 값)을 사용자 실행 스모크에 넣는다. 한 필드에 의존하지 않고 신뢰할 수 있는 신호(헤더)를 우선하되, 없는 경우의 동작(보수적 정산 + 경보)을 테스트한다.
- **강제 수단**: `tests/test_gateway_client.py`(`test_deployment_headers_override_body`, `test_deployment_configuration_ids`: 본문=별칭·헤더=배포 ID 픽스처), `tests/test_smoke_gateway.py::test_stage_checks`, 사용자 실행 `make smoke-fallback`의 `INFO` 진단 줄
- **출처**: `make smoke-fallback` 실측(설계 검토에서 SpendLogs 확인) → m3-gateway-fix

### FP-021 검사 조건이 보수 규칙 때문에 우연히 만족되어 가짜 PASS
- **증상**: `smoke-fallback`의 fallback 단계 합격 조건이 "`agent_fallback` 원장 증가 > 0"이었는데, 미등록 응답 모델을 양쪽 공급사에 정산하는 보수 규칙 때문에 모델 매핑이 실패해도 Anthropic 증가가 > 0이 되어 통과할 수 있었다.
- **원인**: 검사 조건이 한쪽 지표만 보았고, 그 지표가 다른 안전장치(보수 정산)의 부작용으로도 만족됐다.
- **규칙**: 합격 조건은 **서로 독립인 신호를 함께** 본다. 공급사별 증가량(한쪽만 증가, 다른 쪽은 정확히 0)과 미등록 모델 0건, 응답 공급사 일치를 모두 요구하고, 각 신호가 따로 실패하는 테스트(가짜 통과 방지)를 둔다.
- **강제 수단**: `tests/test_smoke_gateway.py`(`test_stage_checks`: 미등록·원장 증가 방향이 틀린 단계는 FAIL, `test_unsupported_or_unmapped`)
- **출처**: `make smoke-fallback` 실측 → m3-gateway-fix step 3

### FP-022 가공한 관측 파일로 도구 출력의 형태를 단정
- **증상**: m3-approval 설계 검토에서 "`lookup_id`는 ID만 돌려준다"고 단정해 V15 근거 정의에 "`lookup_id` 제외"가 들어갔다(ADR-018 8항). 실제 A 기록 `data/replay/a_run/T1-01.jsonl`에는 `lookup_id`가 `syn-meeting-001`의 `text`(667자)까지 돌려줬다. 본문이 모델 입력에 들어갔는데 근거에서 빠지면 분류 하향 유출 방어가 약해진다.
- **원인**: 판단의 근거로 쓴 `data/results/dev/<run>/observations.jsonl`은 러너가 도구 결과 본문을 빼고 구조 ID만 남긴 **가공본**이다(`eval/observation.py`의 `tool_results` 주석). 가공본에서 "본문이 없다"를 "도구가 본문을 돌려주지 않는다"로 읽었다.
- **규칙**: 도구 출력의 형태·내용에 대한 판단은 **원자료**로만 한다: `data/replay/a_run/`(기록한 A 응답), `data/contract/*.v1.json`(계약 스냅샷), live 출력. 가공본(관측 JSONL, 요약, 집계)은 판단 근거로 쓰지 않는다. 설계 초안에서 데이터에 기반한 판단을 적을 때는 **근거 파일 경로를 함께 적는다**.
- **강제 수단**: /review 체크리스트(데이터 판단의 근거가 원자료인가), /harness 설계 초안의 "확인한 사실"에 근거 경로 필수. 규칙이 코드로 옮겨진 곳: `agent/scope.py`의 구조 근거 추출(`text`가 있는 항목)과 그 테스트(`tests/test_agent_scope.py::test_lookup_replay_evidence`: `lookup_id`의 `syn-meeting-001`이 근거에 들어감).
- **출처**: m3-approval 설계 검토, ADR-018 8항은 m3-approval step 8(`a29d25a`)에서 구조 규칙으로 고침

### FP-023 외부 SDK가 같은 오류를 타이밍에 따라 다르게 감싸 올림
- **증상**: m3-faults step 1에서 A MCP 클라이언트의 닫힌 포트(연결 거부)·응답 지연이 지연 없이는 `connect`/`timeout`으로 분류됐지만, SDK 시작을 늦추면 `protocol`로 분류됐다. `protocol`은 `DEFINITIVE`라 재시도하지 않고 서킷 실패 횟수를 0으로 되돌려 A가 죽어도 서킷이 열리지 않을 수 있다. 첫 시도는 같은 문제를 테스트의 sleep·관측 지점 조정으로 맞추려다 3번 막혔다.
- **원인**: MCP SDK는 연결을 백그라운드 태스크에서 열고 `ConnectError`·타임아웃을 그대로 올리기도 하고 연결 종료류 프로토콜 오류로 감싸 올리기도 한다(타이밍 의존). 분류가 SDK가 올려 준 예외 형태에 기댔다.
- **규칙**: SDK가 올려 준 예외 형태에만 기대어 분류하지 않는다. 전송 계층을 감싸 요청 단위로 사실(전송 오류 종류·비정상 상태 코드·`Retry-After`)을 직접 기록하고, 기록된 사실을 예외 해석보다 먼저 쓴다(401 → 전송 오류 → 비정상 HTTP 상태 → 기존 해석 → protocol). 비밀·본문은 기록하지 않는다. 타이밍 의존 실패는 실제 결함이다(FP-011): 지연 없음과 느린 시작 두 조건에서 같은 분류가 나오는지 단언한다.
- **같은 뿌리의 경쟁 조건 사례**: 부하 없는 `make verify`에서는 통과하는 타이밍 의존 단언. m3-faults step 4가 만든 `test_each_f11_variant_has_transport_failure_evidence[delay]`는 `ToolPolicy(hard=0.01)`(10ms)로 읽기 3회 재시도 뒤 `server.requests == 3`을 단언했다. SDK 시작이 10ms를 넘으면 요청이 서버에 닿기 전에 취소돼 서버가 2건만 받았다(step 6의 `make verify`에서 관측, step 4·5에서는 운 좋게 통과). 시간 값만 키우면 느린 CI에서 재발할 수 있다.
- **규칙(추가)**: 서버 도달 수처럼 타이밍에 따라 달라지는 값 대신 **클라이언트 쪽 기록(전송 계층 기록의 시도 횟수)처럼 결정적인 값**을 단언한다. 서버 수신 건수는 시도 횟수 이하로만 확인한다. 작은 시간 값과 느린 시작 조건에서도 통과해야 한다. 단언을 느슨하게 하는 것이 아니라 단언 대상을 결정적인 값으로 바꾼다.
- **강제 수단**: `tests/test_a_mcp_status.py`(장애 7종 × 시작 조건 6종, 서킷 연속 5회 열림), 금지사항에 "SDK 예외 형태에만 기대어 분류하지 마라" 명시, 경쟁 조건 테스트는 m3-faults step 9에서 클라이언트 기록 기준으로 교체
- **출처**: m3-faults step 1(`a93b4d3`로 지시 수정 → `888fa62`), step 6 blocked(`55f71e2`, 수정 `b4707f9`)

## D. 설정·환경·프로세스

### FP-030 빈 환경 변수가 기본값을 덮음
- **증상**: `.env`에 `KEY=`처럼 비워 두면 `os.environ.get("KEY", 기본값)`은 빈 문자열을 돌려줘 기본값이 쓰이지 않았다(`MOCK_JIRA_URL`·`SMOKE_USER`·`AUDIT_DIR`·`A_ROOT`·`GOVOPS_TRACE_DIR`·`MOCK_JIRA_TICKETS`). `.env.example`이 변수 이름을 빈 값으로 안내하므로 사용자가 그대로 쓰면 발생한다.
- **원인**: 기본값 인자가 "설정 안 됨"만 처리하고 "빈 값"은 처리하지 않는다.
- **규칙**: 기본값이 있는 환경 변수 읽기는 `govops.env.env_or(name, default)`로 한다(미설정·빈 문자열·공백뿐이면 기본값). 필수 값은 `os.environ["X"]`로 누락을 드러낸다.
- **강제 수단**: `tests/test_env_defaults.py`(`src/govops/` 전체에서 `environ.get`/`getenv`에 비어 있지 않은 기본값을 넘기거나 `getenv(...) or "<상수>"`를 쓰면 실패, 변수별 빈 값 회귀 테스트)
- **출처**: m3-gateway step 0

### FP-031 실행 직전 작업 트리 변경이 `execute.py`를 막음
- **증상**: 사용자가 실행한 `make smoke-fallback`이 비용 원장(`data/manifest/costs.jsonl`)에 행을 추가해 작업 트리가 더러워졌고, `execute.py`가 시작하지 않았다. 같은 때 step 파일 생성 스크립트가 `/tmp`에서 사라져 phase 파일이 만들어지지 않은 채 `execute.py`가 빈 phase로 실행됐다.
- **원인**: 사용자 실행 명령이 커밋 대상 파일을 갱신하는 것과 phase 실행의 깨끗한 작업 트리 요구가 충돌했다. 일회용 스크래치 파일에 의존한 생성 절차는 세션이 바뀌면 깨진다.
- **규칙**: 비용 원장처럼 커밋 대상인 실행 산출물은 실행 직후 **별도 커밋**(`chore:`)으로 올린 뒤 phase를 실행한다. phase를 만들 때는 생성 절차를 저장소 밖 스크래치에 의존하지 말고, 파일이 실제로 만들어졌는지(`phases/<phase>/index.json` 존재)를 확인한 뒤 실행한다.
- **강제 수단**: `scripts/execute.py` 작업 트리 검사, /harness 파일 생성 후 확인
- **재발**: `make smoke-approval`이 비용 원장에 24줄을 추가했고 이어서 `make eval-run-dev`가 작업 트리 청결 가드(`dirty_worktree`)로 중단됐다(가드는 정상 동작). 원장만 별도 PR(`chore:`, #47)로 올려 해결했다. 후속 검토: smoke 출력 마지막 줄이나 Makefile 주석에 "원장이 변경되었으니 커밋하세요" 안내를 넣고 가드 메시지가 더럽힌 파일을 보여 주는 방안.
- **출처**: m3-gateway-fix 시작 시점, smoke-approval 직후(PR #47)

### FP-032 로컬 통과가 CI 통과를 보장하지 않음 (환경 차이 두 사례)
- **증상**: PR #45가 로컬 `make verify`는 통과했는데 CI에서 두 번 연달아 다른 테스트로 실패했다. (a) `test_superseded_run_evidence`: `git log --diff-filter=A`가 CI의 얕은 복제(`actions/checkout` 기본 `fetch-depth: 1`)에서 단일 커밋만 돌려줬다. (b) `test_real_effect_scripts[jira_add_comment-approve]`: 드라이버 테스트가 가짜 sleep(`sleep(0)`)과 `poll_interval=0.5`를 써서 폴링 360회 만에 가상 시간 180초가 소진됐고, 감사 쓰기(스레드·디스크 I/O)가 느린 CI에서는 그래프가 끝나기 전에 `timed_out`으로 반환해 상태가 `running`으로 남았다.
- **원인**: (a) git 이력에 의존하는 테스트가 CI 체크아웃 깊이를 가정하지 않았다. (b) 가짜 sleep이 가상 시간만 흘려 보내 실제 I/O가 진행될 시간이 없었다. 둘 다 로컬에서는 재현되지 않았다(8회 반복·CPU 부하에서도 통과).
- **규칙**: git 이력·파일 시스템·시간·I/O 지연에 의존하는 테스트는 CI(얕은 복제, 느린 디스크)에서 달라질 수 있음을 가정하고, 실패를 **재현한 뒤에** 고친다(느린 I/O를 주입해 원래 테스트가 실패하고 수정 후 통과함을 확인). 가상 시간을 쓰는 폴링은 폴링마다 실제로 조금 기다리거나(`real_tick`) 가상 대기를 충분히 짧게 한다. 입력을 고정해 가리지 않는다(FP-011).
- **강제 수단**: `.github/workflows/ci.yml`의 checkout `fetch-depth: 0`과 `tests/test_ci_workflow.py::test_ci_checkout_fetches_full_history`, `tests/test_evalrun_driver.py`의 `real_tick`(타임아웃 시나리오 테스트는 그대로)
- **출처**: m3-runner PR #45 CI 실패 2건(`f11bf1e`, `2d50d4e`)

## E. 평가·판정·사전 등록

### FP-040 0건 지표를 "아무것도 안 하기"로 통과
- **증상**: 장애 주입 하 중복 실행 건수만 보면 아무것도 발송하지 않아도 0이다. F2 연속 장애에서 효과 0건이어도 중복 0으로 통과한다.
- **원인**: "적을수록 좋은" 지표 하나로 합격을 판정했다. 지표를 0으로 만드는 가장 쉬운 방법은 일을 하지 않는 것이다.
- **규칙**: 0이 좋은 지표는 **짝 지표**(누락 건수 = Σ max(0, 기대 − 실제), 최종 상태, 승인 표식 없는 효과 수)와 함께 판정·보고한다. 시나리오마다 기대 결과(효과 수·최종 상태)를 사전 등록한다.
- **강제 수단**: `eval/duplicates.py`의 `count_effects`(중복·누락·미승인·마커 없음), `tests/test_duplicates.py::test_conservative_effect_counts`, `tests/test_effects_matrix.py`(시나리오별 중복·누락·최종 상태 단언)
- **출처**: m3-effects 설계 검토(`da3e560`)

### FP-041 상태 매핑이 다른 판정 범주를 오염
- **증상**: 결정론 검사 차단(`blocked`)을 관측에서 `rejected`로 옮기면 TCR의 `approval_rejected`가 "상태가 `rejected`이기만 하면 통과"여서, 검사에 막혀 끝난 T6-01도 거부 시나리오를 통과했다.
- **원인**: 상태를 다른 범주로 옮기는 매핑을 하면서, 옮겨 간 쪽 판정의 통과 조건이 원래 의미(승인자가 실제로 reject했다)를 요구하는지 확인하지 않았다.
- **규칙**: 상태를 다른 판정 범주로 매핑할 때는 그 범주의 통과 조건이 **원래 의미를 증명하는 기록**(받아들여진 reject 기록)을 요구하는지 확인하고, 매핑된 값만으로 통과시키지 않는다. 매핑 표마다 "이 값이 우연히 통과하는 판정"을 목록으로 만들어 테스트한다.
- **강제 수단**: TCR 사유 `reject_not_recorded`, `tests/test_eval_tcr.py`(`test_rejection_requires_accepted_reject_record`, `test_t6_check_block_is_not_an_approval_rejection`, `test_other_outcomes_do_not_require_reject_records`)
- **출처**: m3-approval 설계 검토, step 7(`c7f9506`)

### FP-042 실행 중 코드가 정답지를 읽음
- **증상**: ScopeIndex 초안이 `data/eval/meta.json`을 읽도록 설계됐다. 이 파일에는 `facts`·`canaries`·`injections`(평가 정답·함정)가 있어, 에이전트 실행 경로가 정답지에 접근하게 된다.
- **원인**: 필요한 필드(문서 ID와 접근 필드)가 이미 그 파일에 있다는 편의로 원본을 그대로 읽게 했다.
- **규칙**: 실행 경로(`agent/` 등) 코드는 평가 데이터(`data/eval/`, `govops.eval`)를 읽거나 import하지 않는다. 필요한 최소 필드만 생성 스크립트로 별도 파일(`data/seed/access_index.json`)에 만들어 커밋하고 그 파일에 접근 필드 외 키가 없음을 테스트한다.
- **강제 수단**: `tests/test_agent_data_boundary.py::test_agent_data_boundary`(`agent/`의 `govops.eval` import와 `data/eval`·`meta.json` 문자열 스캔), `tests/test_scope_index.py`(인덱스에 접근 필드 외 키 없음)
- **출처**: m3-approval 설계 검토, step 1(`8224ad6`)

### FP-043 기대값을 실행 뒤에 정함
- **증상(방지한 사례)**: 골든 기대값·가설·목표를 기록·실행 결과를 본 뒤에 정하면 결과에 맞춰 기준이 움직인다. 평가 규칙은 기대값을 실행 전에 정답지에서 계산하고 사전 등록 파일과 **같은 커밋**에 넣도록 정했다.
- **원인(위험)**: 결과를 본 뒤의 등록은 사전 등록이 아니다. 기록이 기대에 실패했을 때 기대값을 고치거나 다시 기록해 통과시키는 유혹이 생긴다.
- **규칙**: 기대값·가설·실행 계획표·목표는 실행 **전에** 정답지(명세)에서 계산해 커밋한다. 러너는 등록 파일이 커밋되어 있지 않으면 실행하지 않는다. 기록이 기대에 실패하면 기대를 고치지 않고 `recorded_fail`로 표시한다. 새 등록은 기존 사전 등록 파일을 수정하지 않고 별도 파일로 쌓는다. **사전 등록 규칙이 의도보다 엄격할 때도 소급해서 고치지 않는다**: 새 등록 파일과 규칙 버전(`grading_rules`)으로 그 파일의 등록 커밋 **이후** 채점에만 적용하고, 기존 실행의 공식값은 등록 규칙 기준으로 보존한다(새 규칙의 재채점 값은 "참고값"으로 나란히 적는다).
- **강제 수단**: 러너 가드 `hypotheses_not_committed`(`evalrun/dev.py`의 `guard_live`, `tests/test_evalrun_dev.py::test_git_guard`), 골든 `expectations.json` 재계산 테스트, m3-faults에서 `hypotheses-m3.md`와 확장 기대값을 같은 커밋으로 둔다
- **사례(규칙이 의도보다 엄격)**: dev T2-01은 요청자가 읽은 `syn-incident-019` 본문에 이미 있는 티켓 ID `PAY-1004`를 되풀이했는데 읽을 수 없는 티켓 ID로 금지 목록에 들어가 `forbidden_leak`로 실패했다(등록 규칙 `forbidden_auto`가 literal 값에만 "읽을 수 있는 문서에도 있는 값은 뺀다"를 적용). 등록 규칙·dev 공식값(TCR 17/20)·기존 골든은 그대로 두고 `docs/results/hypotheses-m3-grading.md`(`grading_rules="m3-grading"`)에 새로 등록해 이후 채점에만 적용했다(참고값 18/20). 가드 `guard_grading_rules`(등록 이후 변경 금지), 테스트: `registered` 재채점이 저장된 결과와 같음.
- **출처**: m2-run step 4(`f09ca0a`), 사전 등록 커밋 `66be7b3`; 규칙 보완 사례는 m3-dev-results step 2(`5ae5c16`)

### FP-044 같은 판정에 진입점이나 규칙 버전이 둘임
- **증상**: 골든 `status.json`은 T2-01을 `pass`로 기록했는데 같은 관측의 dev 채점은 `forbidden_leak`로 실패였다. `golden.tcr_status`가 `grade_tcr`를 직접 불러 채점 시점 금지 목록 자동 계산(`forbidden_auto`)을 거치지 않았다. 진입점을 통일해도 규칙 버전을 고정(`REGISTERED`)해 두면 두 번째 실행(`m3-grading` 공식)에서 dev는 통과인데 골든은 `recorded_fail`이 되는 같은 불일치가 다시 생긴다.
- **원인**: 판정 로직의 단일 진입점(`grade_run`)을 정해 놓고 일부 경로가 우회했고, 규칙 버전을 호출자가 넘기지 않고 상수로 박았다.
- **규칙**: 같은 관측에 대한 판정(dev 채점, 골든 상태, 재채점 도구)은 `grade_run` 하나를 거치고 **규칙 버전(`grading_rules`)은 필수 인자**로 호출자가 그 실행의 값을 넘긴다. 규칙 버전은 manifest에 기록하고 재계산 도구는 대상 실행의 manifest 값을 따른다(값이 없는 첫 실행은 `registered`로 간주하고 그 사실을 출력한다).
- **강제 수단**: `golden.tcr_status`·`record_status`의 필수 `grading_rules`, `tests/test_regolden.py`(골든 상태와 dev 채점이 같은 판정, T2-01형 관측으로 `m3-grading`이면 둘 다 통과·`registered`이면 둘 다 `recorded_fail`)
- **출처**: m3-dev-results step 0(`f408707`)·step 2(`5ae5c16`)

### FP-045 세는 대상 정의가 다른 두 기록을 비교
- **증상**: `tool_trace_mismatch`가 T2-09·T5-11·T6-01 세 건 나서 `n_registration_blocked`가 켜졌다. 캡처는 **실행된** 도구 호출만 기록하고 `messages`의 assistant 호출에는 **제출된** 초안(부작용 도구)이 들어 있어서 초안만 내고 실행되지 않은 task는 항상 개수가 달랐다.
- **원인**: 비교 대상 두 기록이 무엇을 세는지(실행 vs 제출)를 맞추지 않았다.
- **규칙**: 두 기록을 비교하기 전에 양쪽이 세는 대상의 정의를 같게 맞춘다(읽기 도구만 비교, 부작용은 양쪽에서 제외). 정의를 맞춘 뒤에도 진짜 불일치(읽기 호출이 캡처에서 빠짐)는 계속 잡히는지 테스트한다. 비교를 끄거나 허용 오차를 두지 않는다.
- **강제 수단**: `tests/test_evalrun_tool_trace.py`(합성 상태 T2-09형·T5-11형·T6-01형은 불일치 아님, 읽기 호출 누락은 불일치), 저장된 증거 테스트(세 task의 `effects == []`)
- **출처**: m3-dev-results step 1(`b4b27d2`)

## F. 설계·계획·프로세스

### FP-050 설계 승인 전에 외부 계약을 실측하지 않음
- **증상**: 설계를 승인한 뒤 live에서 결함이 드러나 수정 phase가 늘었다. M3는 계획 phase 3개가 live 실행과 검토에서 나온 수정으로 PR 9개가 됐다. 예: LiteLLM 응답 본문 `model`이 별칭이고 실제 모델은 헤더 `x-litellm-model-id`에 있음(m3-gateway-fix), 에이전트 가상 키의 허용 모델 목록 부족, 프록시가 `mock_testing_fallbacks`를 기본 거부.
- **원인**: 설계 초안이 외부 지점(LiteLLM·A MCP·mock·docker·`.env`·기존 코드 진입점)의 실제 응답·원자료를 확인하지 않은 채 가정에 기댔다.
- **규칙**: phase 설계 초안에 **"외부 계약 실측 목록"**을 필수로 넣는다. phase가 의존하는 외부 지점마다 실제 응답이나 원자료를 근거 경로와 함께 확인한 결과를 적는다. 구현 뒤 /review가 목록과 구현을 대조한다.
- **재발(성공 여부만 확인)**: m3-faults 설계 실측이 "`faults set`·`stats`·`clear` 명령이 성공한다(exit 0)"만 확인하고 "기대한 값을 돌려주는가"는 확인하지 않았다. 실제 `faults stats`는 요청 카운터만 돌려주고 규칙 목록은 읽을 수 없으며 `reset`이 규칙을 지운다. step 6이 "남은 규칙 탐지"·"stats에서 규칙 확인"을 지시해 blocked 됐고(`7bede9b`), step 4의 `stale_faults` 검사(clear 직후 stats가 항상 `{}`)는 절대 걸리지 않는 검사였다(FP-005·FP-021 유형, 삭제로 결정).
- **규칙(추가)**: 실측 목록에는 **성공 여부(exit 0·응답 코드)와 반환 내용(응답 값·필드의 의미)을 따로 적는다.** 반환 내용은 원자료(코드·실제 출력)로 확인한다.
- **강제 수단**: /harness 설계 초안 필수 항목과 자체 점검 (4)(성공 여부와 반환 내용 분리), /review 체크리스트 10번
- **출처**: m3-gateway-fix(PR #41), M3 PR 계획 3 → 9, m3-faults step 6(`c818226`)

### FP-051 첫 live 실행에서 터질 것을 미리 따지지 않음
- **증상**: 첫 live 실행 전에 확인하지 않은 환경 문제(빈 `.env` 값이 기본값을 덮음, 꺼진 컨테이너, 옛 mock 이미지, LiteLLM 미재시작)가 결과를 가짜로 만들 수 있었다.
- **원인**: 실행 환경이 현재 코드·설정을 반영하는지 확인하는 단계가 없었다.
- **규칙**: 설계 초안에 **"첫 live에서 터질 수 있는 것"** 목록과 각 항목을 막는 preflight·smoke 대응을 적는다. 러너는 환경이 낡았으면 이유 코드를 출력하고 시작하지 않는다.
- **강제 수단**: `evalrun/preflight.py`의 `stale_mock_image`·`litellm_config_stale`·`service_down:<이름>`(`tests/test_evalrun_preflight.py`), `env_or`(FP-030), `smoke-fallback`·`smoke-approval`
- **출처**: m3-runner step 3(preflight), m3-gateway

### FP-052 머지 후 사용자 실행 순서 누락
- **증상**: mock 코드 변경 뒤 컨테이너 재빌드, LiteLLM 설정 변경 뒤 재시작, 가상 키 허용 모델 변경 뒤 키 갱신을 사용자가 알지 못하면 새 코드가 옛 환경에서 실행된다.
- **규칙**: PR 본문의 "머지 후 사용자 실행" 절에 재빌드·재시작·키 갱신 필요 여부를 항목으로 점검한다. 필요하면 그 순서를 명령까지 적는다.
- **강제 수단**: /review 체크리스트 11번(PR 본문의 해당 절 확인)
- **출처**: m3-gateway(PR #40), m3-gateway-fix(PR #41: `docker compose up -d litellm` → `make agent-key-update` → `make smoke-fallback`)

### FP-053 수정마다 새 phase를 만들어 왕복이 늘어남
- **증상**: 설계 검토에서 나온 수정이나 live에서 드러난 결함마다 새 phase를 만들어 PR·CI·머지 왕복이 늘었다(`m3-gateway-fix`, `m3-dev-results` 등).
- **규칙**: 수정은 새 phase를 만들지 않고 **다음 계획된 phase의 step으로 넣는다**. 코드는 계속 Codex 하네스에 맡긴다.
- **강제 수단**: /harness 설계 원칙 8
- **출처**: M3 진행 회고

### FP-054 step 파일의 지시가 자기모순이거나 규칙과 충돌
- **증상**: step이 `blocked`로 멈췄다. (a) m3-runner step 3: 같은 파일의 "러너는 `audit`·`guardrails`를 직접 조립하라"가 `AGENTS.md` 계층 경계 표(`evalrun`은 `audit`·`guardrails` import 금지)와 충돌. (b) 같은 step의 "M2 러너 수정 금지"(읽기 목록·작업 서두·금지사항)와 "`_structure`를 공용 모듈로 옮겨 M2가 import"(0번 항목)가 충돌. (c) m3-faults step 3: "`evalrun`에서 `mocks`의 `Rule`·`Action`으로 규칙을 검증하라"가 계층 경계(`evalrun`은 `mocks` import 금지)와 충돌해 `blocked`. (d) m3-faults step 2: F11 합성 토큰 규칙에 필요한 `AMcpClient.credential_for` 주입을 위해 step이 `tools/a_mcp.py`를 수정했는데 step 파일은 그 파일을 읽기 목록에만 넣어 **수정 허용 범위가 누락**돼 있었다(설계 누락, 변경은 유지). (e) m3-faults step 4: 작업의 "하네스가 live A에 접속하는 코드를 작성하라"와 금지사항의 "접속하는 테스트나 코드 실행 금지"가 **작성과 실행을 구분하지 않아** 충돌(`96bff0a`). (f) step 6: 작업 4의 "`down_alias` 호출이 원장에 기록"과 FP-031 금지사항의 "원장을 코드 경로에서 갱신 금지"가 충돌(`cbd26c2`). 지시 충돌 blocked가 **코드를 쓴 뒤에야** 드러나 시도당 20~30분이 들었다(step 3·4·6). 금지사항에 FP를 10개 넘게 넓게 옮긴 것이 모순과 길이 증가의 한 원인이었다.
- **원인**: step 파일을 쓴 뒤 금지사항과 작업의 모순, 계층 경계 표와의 충돌, 수정해야 할 파일의 누락을 점검하지 않았다.
- **규칙**: step 파일을 쓴 뒤 **자체 점검**을 돌린다. step마다 "수정할 파일" 목록을 적고(이전 step의 산출물을 고쳐야 하면 그 파일도 적는다) 그 목록을 (1) 금지사항과 작업의 모순, (2) `AGENTS.md` 계층 경계 표와 `tests/test_architecture.py`의 `FORBIDDEN_IMPORTS`, (3) 읽기 전용 목록(`.claude/`·사전 등록 파일·`data/replay` 기록)과 대조해 충돌을 고친 뒤 커밋한다. 예외가 필요하면 읽기 목록·작업·금지사항 세 곳에 같은 표현으로 허용 범위를 적는다. 점검 결과(step별 수정 파일, 발견한 충돌과 고친 내용)를 사용자 보고에 넣는다. 목록 밖 수정이 필요하면 Codex는 임의로 우회하지 않고 `blocked`로 보고한다.
- **규칙(추가)**: 모든 step 파일은 /harness 양식의 **공통 금지사항 고정 블록**을 수정 없이 복사한다. 블록의 맨 앞 항목은 **선점검**이다: 코드를 쓰기 전에 작업 지시·금지사항·"수정할 파일" 목록을 대조하고, 충돌이 있으면 코드를 쓰지 않고 즉시 `blocked`로 보고하며 reason에 충돌하는 두 문장을 인용한다. 블록에 있는 내용은 step별로 반복하지 않고, step별 금지사항에는 그 step의 수정 파일과 작업에 실제로 해당하는 FP만 5개 안팎으로 옮긴다. "작성 요구"와 "실행·갱신 금지"는 구분해 적는다.
- **강제 수단**: /harness 설계 원칙 9(자체 점검)와 양식의 공통 금지사항 블록(`<!-- common-block -->` 표식), `scripts/test_step_files.py`(`## 공통 금지사항` 제목이 있는 step 파일은 블록을 수정 없이 복사했고 맨 앞이 선점검인지 검사), step 파일의 "수정할 파일" 목록과 "목록 밖 수정 금지" 금지사항(m3-faults step 9부터 블록에 통합)
- **출처**: m3-runner step 3 blocked 2회(`639a5c7`, `8954223`), m3-faults step 3 blocked(`32d8aa6`, 수정 `2743bec`), step 2 범위 누락(`e054427`), step 4 blocked(`96bff0a`, 수정 `25ae632`), step 6 blocked 3회(`cbd26c2`·`7bede9b`·`55f71e2`, 수정 `31c52c5`·`c818226`·`b4707f9`)

### FP-055 읽기 목록의 파일을 Codex가 지시 없이 수정
- **증상**: m3-dev-results step 2에서 Codex가 읽기 목록에 있던 `.claude/skills/failure-patterns/SKILL.md`의 FP-043에 한 줄을 추가했다(내용은 맞았으나 지시가 없었다).
- **원인**: step 공통 금지사항에 `.claude/`와 사전 등록 파일 수정 금지가 없었다.
- **규칙**: step 파일의 공통 금지사항에 ".claude/(skills·commands)와 `docs/results/`의 사전 등록 파일은 읽기만 하고 수정하지 않는다(step이 명시적으로 허용한 경우 제외)"를 넣는다. /review는 step 범위 밖 변경 파일을 확인한다.
- **강제 수단**: /harness step 템플릿의 공통 금지사항, /review 체크리스트 9번
- **출처**: m3-dev-results step 2(`5ae5c16`)

### FP-056 중간 step에서 완화한 테스트를 되돌리지 않고 진행
- **증상**: m3-runner step 4가 `test_registered_expectations_match_specs`를 `GOLDEN_TASKS[:7]`·`require_all=False`의 부분 비교로 완화했다(그 시점 `expectations.json`이 7개뿐이어서). 다음 step에서 14개가 되었는데도 완화된 채 남아 있었다.
- **규칙**: 테스트를 완화할 때는 복원 조건을 주석으로 남기고 그 조건이 해소되는 step에서 복원한다. /review는 완화된 단언(`require_all=False`, 슬라이스 비교)을 찾는다. 복원 뒤 실패하면 테스트를 고치지 말고 어느 항목이 명세와 다른지 보고한다.
- **강제 수단**: /review 체크리스트 7번(완화된 단언 확인), `tests/test_golden_trace.py`(14개 전체 비교로 복원, `fc65bcd`)
- **출처**: m3-runner step 4(`a58005a`) → 복원 `fc65bcd`

### FP-057 Stop hook이 실행 중인 phase의 중간 작업 트리를 검증해 세션을 막음
- **증상**: `execute.py`가 Codex로 step을 실행하는 동안 Claude Code 세션의 Stop hook(`make verify`)이 중간 작업 트리를 검증해 수시로 실패(최대 44건)하고 "수정을 계속하라"고 되돌렸다. 실패는 Codex가 편집 중인 파일의 중간 상태였는데 hook 메시지가 Claude 세션이 직접 고치도록 유도했다(동시 편집 위험).
- **원인**: hook이 "실행 중인 phase가 있는가"를 알지 못한다. 변경된 파일이 있으면 무조건 검증한다.
- **규칙**: `execute.py`는 시작할 때 gitignore된 잠금 파일(`.harness/executing`)에 PID를 적고 끝날 때 지운다. Stop hook은 잠금의 PID가 살아 있고 환경 변수 `HARNESS_EXECUTOR_PID`(execute.py가 잠금을 잡을 때 설정해 자식 프로세스가 상속)가 그 PID와 다르면 검증을 건너뛴다(비정상 종료로 PID가 죽어 있으면 잠금은 무효). 실행기의 자손(step을 실행하는 Codex 세션)은 환경 변수가 같으므로 계속 검증해 스스로 고친다. 조상 프로세스를 `ps`로 찾는 방식은 쓰지 않는다: Codex 샌드박스가 `ps`를 막아 `make verify`가 권한 요청 뒤에야 통과했다(step 4~8 요약). 중간 Stop hook 실패에는 대응하지 않는다(Codex가 편집 중인 파일을 고치지 않는다).
- **강제 수단**: `scripts/test_hooks.py`(살아 있는 외부 PID → `make verify` 없이 exit 0, 죽은 PID·`HARNESS_EXECUTOR_PID`가 잠금 PID와 같은 경우·깨진 잠금 → 정상 검증, `ps`가 막혀 있어도 판정이 같음), `scripts/test_execute.py::TestExecutionLock`(잠금 생성·해제·중복 실행 거부)
- **출처**: m3-faults batch 0-3

### FP-058 완료 신호 없는 실행 방식으로 대기
- **증상**: `execute.py`를 `(nohup … &)`로 띄우고 Monitor를 켰다가 Monitor를 끄자 깨울 신호가 없어 step 3 완료를 한참 뒤에야 알아챘다. 같은 방식으로 띄운 `make verify`도 종료 알림이 오지 않았다.
- **원인**: 도구가 추적하지 않는 분리된 프로세스는 종료 알림이 없다.
- **규칙**: `execute.py`와 장시간 명령은 Bash `run_in_background: true`로 실행해 종료 시 자동 알림을 받는다. 이미 분리돼 돌고 있는 경우에만 Monitor를 쓰고, 주기 출력 없이 프로세스 종료나 step 상태 변경 때 한 번만 알리는 until 루프로 쓴다. 기다리는 동안 상태를 반복 조회하지 않는다.
- **강제 수단**: /harness E(실행) 절의 실행 안내
- **출처**: m3-faults batch 0-3 step 3

### FP-059 금지 문장의 범위 미지정
- **증상**: m4-guardrail에서 같은 phase의 step이 세 번 `blocked`됐다. (a) 금지 "등록 문서에 결과 수치·TCR을 쓰지 마라"와 작업 "비교 기준 TCR 18/20을 적어라"(이미 확정된 이전 공식 결과와 이번 실행 결과를 구별하지 않음). (b) 금지 "`make` 타깃을 실행하지 마라"와 AC `make verify`(어떤 타깃인지 이름이 없었다). (c) 금지 "규칙 파일 내용을 열람하지 마라"와 "테스트 코드가 규칙 파일을 프로그램으로 읽어 등록 문서와 비교한다"(사람·Codex의 열람과 코드의 읽기를 구별하지 않음).
- **원인**: 금지 문장이 **대상(무엇을)·주체(누가)·범위(어디까지)**를 적지 않았고 예외를 같은 문장에 두지 않았다. Codex는 선점검에서 두 문장을 그대로 충돌로 읽고 멈춘다(FP-054).
- **규칙**: 금지 문장에는 대상을 이름으로 적는다(실행 금지는 `make 타깃 이름`·`python -m …`을 하나씩, 범주 표현·글롭 금지), 주체(Codex 직접 열람 vs 테스트 코드의 읽기)와 범위(이번 실행의 결과 vs 확정된 이전 값)를 구별하고 예외는 별도 문장으로 분리한다. 금지 경로는 `열람 금지 경로:` 줄로 선언한다.
- **강제 수단**: `scripts/step_file_checks.py::command_violations`(실행 금지 문장이 명령을 명시하지 않거나 범주·글롭 표현이면 실패, AC·검증 명령과 겹치면 실패), `read_list_violations`, `scripts/test_step_files.py`(형식 v2 phase 전 step 파일에 적용, 합성 사례 테스트 포함). 의미상의 모순(결과 수치 vs 이전 공식 값)은 기계가 못 잡으므로 설계 검토에서 대상·주체·범위를 확인한다.
- **출처**: m4-guardrail step 6 blocked 1차(`2997bf0`)·step 10 blocked(`abe5d9a`, 읽기 목록의 규칙 파일)

### FP-060 닫힌 수정 파일 목록에서 소비자 테스트 누락
- **증상**: step이 기존 심볼·파일·상태를 바꾸는데 그것을 단언하는 기존 테스트가 "수정할 파일(이 목록이 전부다)"에 없어 `make verify`가 실패하고 step이 `blocked`됐다. 사례: 새 상태 `blocked_input`을 `STATUS_MAP`에 더하자 `tests/test_prereg_m3.py::test_registration_topics`(모든 항목이 M3 등록 표에 있어야 함)가 실패했다. 다른 소비자(ADR-009 변경 기록 항목 수 단언, `test_env_defaults.py`의 변수 표, `load_tickets` 소비자)는 사후 점검에서 같은 방식으로 찾아야 했다.
- **원인**: 설계 때 바꿀 모듈의 소비자를 `rg`로 찾지 않았다.
- **규칙**: step마다 바꾸거나 추가하는 심볼·파일·환경 변수·Makefile 타깃·문서 문구의 소비자 테스트를 `tests/`·`scripts/`에서 찾아 수정 허용 경로에 "~에 한함, 단언 약화 금지" 형식으로 넣는다. 예외가 필요하면 단언을 느슨하게 하지 말고 **예외 집합을 정확히 고정**한다(예: 차이가 정확히 `{"blocked_input"}`).
- **강제 수단**: `scripts/step_file_checks.py::consumer_warnings`가 수정 허용 경로의 소비자 테스트 중 허용 목록에 없는 것을 **경고**로 출력한다(실패 아님, 설계 검토 때 `python scripts/step_file_checks.py phases/<phase>/step*.md`로 확인). 합성 저장소 테스트: `test_consumer_warning_lists_tests_missing_from_the_allowed_line`.
- **출처**: m4-guardrail step 6 blocked 2차(`84c9882`, 진행분 보존 `a52c62e`), 설계 검토에서 step 8·9·10 소비자 보강

### FP-061 앞 step 테스트의 고정 개수 단언과 뒤 step의 충돌
- **증상**: step 8이 만든 경계 테스트가 `eval/redteam*.py`가 정확히 2개(`len(files) == 2`)라고 단언했는데 step 9가 `redteam_cards.py`를 추가해 3개가 되어 step 9가 `blocked`됐다. 같은 유형: ADR-009 변경 기록 항목 수를 정확히 1로 단언한 테스트와 뒤 step의 문서 추가.
- **원인**: 앞 step이 "지금 있는 파일·항목 수"를 단언으로 굳혔고, 뒤 step의 신규 파일·문서 항목이 그 단언을 깨는지 설계에서 점검하지 않았다.
- **규칙**: 개수 대신 **이름 집합**을 단언하고(새 항목은 그 step이 집합을 갱신), 검사 대상 자체(예: 새 파일에도 같은 경계 검사)는 유지한다. 뒤 step의 수정 허용 경로에 그 테스트를 "집합 단언 갱신에 한함"으로 넣는다.
- **강제 수단**: `consumer_warnings`의 신규 파일 점검 — 새 파일을 `glob`·`rglob`로 훑는 테스트(같은 디렉터리 이름을 언급)를 경고한다(`test_consumer_warning_finds_glob_and_count_tests_for_a_new_file`). 문서 항목 수 단언은 경고되지 않으므로 설계 검토에서 `rg "== \d+"`로 확인한다.
- **출처**: m4-guardrail step 9 blocked(`9043e3c`, 진행분 보존 `b425b86`), 같은 유형으로 step 10의 ADR-009 항목 수 단언을 설계 보강

### FP-062 읽어야 할 파일과 금지사항의 충돌
- **증상**: step 10의 "읽어야 할 파일"이 `/src/govops/guardrails/`(`…·rules_v1.json`)을 지정했는데 같은 파일의 금지사항은 "규칙 파일의 내용·패턴을 Codex가 직접 열람하지 않는다"였다. Codex가 코드 작성 전에 두 문장을 인용하며 멈췄다(step 6에서도 읽기 목록의 `rules_v1.json`을 미리 지웠다).
- **원인**: 읽기 목록을 쓸 때 같은 step의 금지사항과 대조하지 않았다. 상위 디렉터리와 파일 이름을 나열한 형태는 금지 경로를 가리키는지 눈으로 알아보기 어렵다.
- **규칙**: 열람을 금지한 경로는 `열람 금지 경로:` 줄에 선언하고 읽기 목록에 그 경로(또는 그 안의 파일·그 이름)를 넣지 않는다. 상위 디렉터리를 읽게 해야 하면 "~내용은 열람하지 않는다"로 범위를 좁힌 문장을 같은 줄에 쓴다.
- **강제 수단**: `scripts/step_file_checks.py::read_list_violations`(금지 경로 안의 항목, 상위 디렉터리, 파일 이름 언급을 잡고 "열람하지 않는다" 문구로 좁힌 줄은 허용), `scripts/test_step_files.py::test_read_list_parent_dir_listing_the_forbidden_name_fails_unless_negated`(실제로 막힌 줄을 재현).
- **출처**: m4-guardrail step 10 blocked(`abe5d9a`)

### FP-063 규칙을 기계적으로 적용해 코드 변경 없는 커밋에도 전체 verify와 별도 PR을 반복
- **증상**: 비용 원장 4줄 때문에 로컬 `make verify` 8분과 CI 10분을 기다렸고, 원장만 올리는 PR이 M3에서 세 번 반복됐다(FP-031 재발).
- **원인**: "커밋 전 verify"와 "청결한 작업 트리" 규칙을 변경 내용과 무관하게 적용했다.
- **규칙**: 코드가 아닌 실행 산출물(원장 하나)만 바뀐 경우를 예외로 정하고, 예외 목록은 **한 파일**에 둔다. 원장은 다음 결과 커밋에 함께 넣고 원장만 올리는 PR은 만들지 않는다. 예외는 원장 하나뿐이며 `docs/`·`data/results/`·`data/replay/`는 테스트가 읽으므로 포함하지 않는다.
- **강제 수단**: `scripts/exempt_paths.json`(단일 출처), `scripts/execute.py`·`scripts/hooks/verify-before-stop.sh`·`evalrun/worktree.py`가 같은 목록을 읽음, `tests/test_evalrun_worktree.py::test_agents_matches_single_source`, FP-064의 호출 지점 전수 점검
- **출처**: m3-faults(원장 PR #47·#57 등), m4-guardrail step 1

### FP-064 예외 목록을 적용한 청결 검사가 일부 호출 지점에서 빠짐
- **증상**: live dev가 `grading_rules_not_committed`로 시작 전에 막혔다. `guard_grading_rules`가 `git status --porcelain`(작업 트리 전체)이 비어 있기를 요구해, smoke가 쓴 원장 한 줄(예외 대상)로 실패했다. step 1이 고친 청결 검사 3곳 밖의 **네 번째 지점**이었다(핫픽스 PR #65).
- **원인**: 같은 의미의 검사(작업 트리 전체 청결)가 여러 곳에 따로 구현돼 있었고, 예외 목록을 도입할 때 호출 지점을 전수 점검하지 않았다.
- **규칙**: 작업 트리 전체의 청결은 `worktree.dirty_entries`를 거친다. 가드가 보려는 대상이 특정 파일이면 `git status --porcelain -- <파일>`로 범위를 **한정**한다(전체 청결은 한 곳이 맡는다). 같은 의미의 검사를 도입·변경할 때는 `src/` 전체의 호출 지점을 목록으로 만들어 확인한다.
- **강제 수단**: `tests/test_git_status_sites.py::test_whole_tree_status_checks_apply_the_exemption_list`(`src/`의 `--porcelain` 호출은 경로 한정이거나 `dirty_entries`를 거쳐야 하며, 핫픽스 이전 코드에서 실패함을 확인), `tests/test_eval_run_grade.py`의 원장만 바뀐 경우 통과·등록 파일 변경/미커밋 실패 테스트
- **출처**: m4-guardrail 후속 live dev(핫픽스 PR #65)

### FP-065 훅의 예기치 않은 예외가 LiteLLM 응답 본문에 그대로 노출됨
- **증상**: 실측(LiteLLM v1.103.3)에서 사전·사후 훅의 예기치 않은 예외는 HTTP 500이고 `error.message`에 예외 문장 원문이 실렸다. 훅이 감싸지 않으면 사후 훅 오류도 fail-open이 아니다. 사전 훅에는 자체 타임아웃도 없다.
- **원인**: 훅을 일반 파이썬 함수처럼 쓰고 호출 경계(프록시)가 예외를 어떻게 응답에 옮기는지 확인하지 않았다.
- **규칙**: 어댑터가 사전 훅은 `wait_for` 타임아웃과 try/except로 감싸 `guardrail_unavailable`(fail-closed)로, 사후 훅은 try/except로 통과 + 경보(fail-open)로 바꾼다. 예외 메시지·스택·입력을 응답이나 로그에 남기지 않는다.
- **강제 수단**: `tests/test_gateway_guardrail_hook.py`(`raise`·`slow`·`post_raise` 프로브와 응답 본문·헤더·로그에 예외 문장 없음), `tests/test_gateway_hook_imports.py`(훅이 import하는 govops 모듈 허용 목록), `make smoke-guardrail`의 `fail_closed` 단계
- **출처**: m4-guardrail 설계 실측, step 3

### FP-066 시간 제한 없는 대기로 CI가 멈춤
- **증상**: PR #63의 CI가 `make verify`의 pytest 95%에서 13분 넘게 멈췄다(직전 CI는 6분). 로그는 `-q` 점 출력뿐이라 멈춘 테스트 이름이 남지 않았고, 취소 시 정리된 고아 프로세스에 워커보다 늦게 생긴 파이썬 하나가 있었다. 같은 PR을 다시 돌리자 멈추지 않았다(간헐적, 원인 미확정). 의심 지점은 `multiprocessing` spawn `Pool.starmap`을 시간 제한 없이 기다리는 `test_processes_never_reserve_beyond_cap`이다(풀 워커가 죽으면 `starmap`은 끝나지 않는다).
- **원인**: job·테스트·서브프로세스·풀 대기에 상한이 없었다.
- **규칙**: CI job에 `timeout-minutes`, 테스트에 `pytest-timeout`(thread 방식: 멈춘 테스트가 스택을 남기고 워커를 끝내 xdist가 이름을 보고함), 풀·스레드·서브프로세스 대기에는 항상 유한 대기를 둔다(`starmap_async().get(timeout=)`+`finally terminate/join`, `subprocess.run(timeout=)`, `join(timeout=)`). 정당하게 오래 걸리는 테스트는 전역 값을 올리지 말고 그 테스트에만 `@pytest.mark.timeout(N)`을 붙인다. **원인을 확인하지 못했으면 확정했다고 적지 않는다**(의심으로 표시).
- **강제 수단**: `.github/workflows/ci.yml`의 `timeout-minutes: 20`, `pyproject.toml`의 `timeout = 120`·`timeout_method = "thread"`, `tests/test_ci_workflow.py::test_ci_verify_job_has_a_timeout`·`test_pytest_per_test_timeout_is_configured`, 서브프로세스 호출 35건과 `Popen.wait` 7곳의 timeout, `tests/test_costs_reservation.py`의 유한 대기
- **출처**: PR #63 CI 정지(실행 `37638207792` 취소), 같은 PR 후속 커밋 `90b655f`

---

## A에서 이식한 항목 (프로젝트 A `failure-patterns`의 번호)

B에서도 그대로 적용되는 패턴이다. 자세한 증상과 근거는 A 저장소 `.claude/skills/failure-patterns/SKILL.md`를 참조한다(A 저장소는 수정하지 않는다).

- **A-FP-011 외부 라이브러리 응답·예외를 추측**: 실제 응답·손상 입력 1건으로 확인한 뒤 fixture에 반영한다(B에서는 FP-020).
- **A-FP-030 항목 하나의 실패가 배치 전체를 멈춤**: 항목 단위 실패는 기록하고 계속한다. 진행이 불가능한 단계만 전체 중단한다(장애 하네스·dev 러너).
- **A-FP-032 append-only 기록의 중복 줄을 그대로 읽음**: 비용 원장·감사 로그(JSONL)를 읽는 코드는 키를 명시해 중복·미정산 예약을 처리한다.
- **A-FP-033 멱등성 키와 출력 키 불일치**: 멱등 판단 키는 출력을 결정하는 키와 같아야 한다(B: task·도구·정규화 인자 해시와 outbox).
- **A-FP-035 전송 후 실패를 재시도해 이중 과금**: B의 "재시도 전 표식 2단계 조회, 바로 재시도는 연결 실패와 429뿐"과 같은 원칙이다(AGENTS.md CRITICAL).
- **A-FP-037 과금 원장을 공급자 청구와 대조하지 않음**: 첫 live 실행 뒤 콘솔 청구와 원장을 대조하고 차이는 여유로 반영한다(ADR-007의 `billing_margin`).
- **A-FP-040 지시에 없는 네트워크 요청·다운로드**: 실행 전에 묻는다. 실제 API 호출은 사용자가 실행한다(AGENTS.md CRITICAL).
- **A-FP-041 세션 간 전달한 수정 요청이 반영되지 않음**: 여러 항목 요청은 항목별 반영 여부와 커밋을 보고하고, 검토는 이전 요청을 커밋과 대조한다(/review 8번).
- **A-FP-042 step 실행 중 작업 트리 변경**: phase 실행 중에는 작업 트리를 건드리지 않는다(B에서는 FP-031).
- **A-FP-043 모호한 수량 기준**: step 설계에서 수량은 무엇을 세는지까지 적는다(예: 장애 주입 분모 132회는 "절대 규칙 1 대상 실행 수").
- **A-FP-044 환경에 좌우되는 결과에 측정 환경을 기록하지 않음**: 결과 문서에 날짜·커밋·모드·모델 ID·A HEAD·계약 스냅샷 해시·인덱스 이름·분모를 적는다(EVAL.md "결과 보고").
- **A-FP-056 실행 경로의 설정 로딩을 키 주입 테스트로만 검증해 `.env` 격리 결함을 놓침**: 비용이 드는 실행 경로의 설정 로딩은 실제 `.env` 경로로 테스트하되 개발자의 실제 `.env`를 읽지 않도록 임시 `.env`를 쓴다.
