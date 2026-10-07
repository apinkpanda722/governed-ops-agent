# Step 8: mask-identifiers

## 읽어야 할 파일

- `/AGENTS.md`, `/docs/ADR.md`(ADR-018·019·020·021), `/docs/EVAL.md`, `/docs/THREAT_MODEL.md`("F1~F11" 표), `/docs/results/hypotheses.md`, `/docs/results/hypotheses-m3.md`, `/docs/results/2026-10-06-m3-dev2-live.md`, `/.claude/skills/failure-patterns/SKILL.md`
- `/src/govops/obs/mask.py`(`mask_attributes`·`_walk`·`_clean`·`mask_text`), `/src/govops/obs/trace.py`(`set_attributes`·trace_id·task_id 형식 `[0-9a-f]{32}`), `/src/govops/obs/jsonl.py`·`/src/govops/obs/langfuse.py`(마스킹 호출 경로), `/src/govops/guardrails/pii.py`(`find_pii`·`mask_pii`), `/src/govops/gateway/client.py`(`generation_id = secrets.token_hex(8)`, span 속성 `generation_id`), `/src/govops/costs.py`(`DEPLOYMENT_MODELS`), `/litellm/config.yaml`(배포 ID `model_info.id`, 읽기 전용), `/tests/test_obs_mask.py`, `/tests/test_gateway_client.py`(`test_generation_id_is_short_unique_and_recorded_on_the_span`), `/tests/test_audit.py`(`test_random_uuid_identifiers`: 같은 문제의 앞선 수정과 대량 테스트 방식)
- **실측된 결함**(2026-10-07 Stop hook 실행에서 관측): `tests/test_gateway_client.py::test_generation_id_is_short_unique_and_recorded_on_the_span`가 `assert root.attributes["generation_id"] == seen[-1]`에서 `'cf[PII:mobile]bed2' == 'cf0169859044bed2'`로 실패했다. `gateway/client.py`가 `secrets.token_hex(8)`을 span 속성 `generation_id`로 남기고 `obs/mask.py`가 문자열 속성 전체에 `find_pii`를 돌려 무작위 숫자 연속(`0169859044`)이 전화번호 규칙에 걸려 가려졌다. 마스킹이 trace 연결(generation_id ↔ Langfuse observation)을 끊고 CI를 무작위로 실패시킨다(FP-010 재발).

## 작업

**배경**: 형식이 정해진 식별자 속성은 PII 검사가 아니라 **형식 검증**의 대상이다(FP-010). `obs/mask.py`가 키와 형식이 모두 맞는 식별자에는 PII 검사를 건너뛰게 한다. TDD: 아래 테스트를 먼저 쓴다.

**수정할 파일**(이 목록이 전부다): `src/govops/obs/mask.py`, 테스트 `tests/test_obs_mask.py`(기존 파일에 추가), 문서 `docs/ADR.md`(ADR-022에 결정 한 항목 추가)·`docs/ARCHITECTURE.md`(obs 마스킹 설명에 한 줄). `tests/test_gateway_client.py`의 기존 테스트는 **수정하지 않는다**(입력 고정 금지, FP-011).

### 1. `obs/mask.py`

- 식별자 속성 표를 코드 상수 한 곳에 둔다: 키 → 형식. `generation_id`: `^[0-9a-f]{16}$`, `trace_id`·`task_id`: `^[0-9a-f]{32}$`, 해시류(`prompt_hash`·`content_hash` 등 코드에서 실제로 span 속성으로 쓰이는 해시 키): `^[0-9a-f]{64}$`, `deployment_id`: **등록 목록**(`costs.DEPLOYMENT_MODELS`의 키와 `litellm/config.yaml`의 배포 ID에 있는 `openai-gpt-6-luna-down`을 포함하는 상수 집합; `obs`에서 `costs`를 import하지 않고 상수로 두며, 테스트가 `DEPLOYMENT_MODELS` 키가 모두 포함되는지 대조한다). 어떤 키가 실제 span 속성으로 쓰이는지는 `set_attributes(`·`attributes[` 사용처를 grep으로 확인해 표를 채운다(추측 금지).
- `_walk`는 `Mapping`의 값이 `str`이고 **키가 표에 있으며 값이 그 키의 형식에 맞을 때에 한해** `mask_pii`를 건너뛴다. 비밀 값 제거(`scrub_secrets`: 등록된 비밀·`Bearer`·`sk-` 등)는 그대로 적용한다. 키가 표에 없거나 형식이 맞지 않는 값은 **지금처럼 PII 검사를 한다**(키만 맞고 값이 형식 밖이면 검사한다).
- 민감 키(`authorization|token|secret|api_key|password|cookie`)의 `[REDACTED]` 처리와 기존 정책(`input.request` 등 역할별 마스킹)은 바꾸지 않는다.

### 2. 테스트 (`tests/test_obs_mask.py`)

- **무작위 대량**: 시드 고정 `secrets.token_hex(8)` 대체(`random.Random(시드).randbytes(8).hex()`)와 `uuid.UUID(int=...)`로 만든 `uuid4().hex` 형식 값 **각 10,000개**가 `generation_id`·`task_id`·`trace_id` 키에서 **가려지지 않는다**(이전 구현에서 약 1%가 실패해야 하므로, 이 테스트가 수정 전 코드에서 실패함을 먼저 확인한다 — FP-011).
- **형식 밖 값**: 같은 키라도 형식에 맞지 않는 값(`generation_id`에 전화번호가 섞인 문자열, 길이가 다른 hex, 대문자 hex)은 PII 검사를 받아 가려진다. 키가 표에 없는 속성에 hex 형식 값을 넣으면 지금처럼 검사한다.
- 해시 64자와 `deployment_id`(등록 목록 값 통과, 목록 밖 값은 검사), 비밀 값 제거가 식별자 키에서도 동작한다.
- **실제 경로 확인**: `obs.trace`로 만든 trace의 span 속성에 `generation_id`를 넣고 sink가 받은 record에서 값이 그대로임을 단언한다(`mask_attributes`를 거치는 실제 sink 경로. 마스킹을 거치지 않는 가짜 sink로만 검증하지 않는다, FP-020).
- 기존 `tests/test_gateway_client.py::test_generation_id_is_short_unique_and_recorded_on_the_span`는 **수정 없이** 통과한다(여러 번 반복해 돌려도 무작위 실패가 없다. 반복 실행은 로컬 확인용이며 테스트 파일에 반복을 넣어 입력을 고정하지 않는다).

### 3. 문서

- `docs/ADR.md`의 ADR-022에 한 항목을 추가한다: "trace 마스킹은 키와 형식이 모두 맞는 식별자 속성(`generation_id`·`trace_id`·`task_id`·해시·등록된 `deployment_id`)에는 PII 검사를 건너뛴다. 형식 밖 값은 검사한다."(결정 내용만, 경위·누가 결정했는지는 쓰지 않는다.) `docs/ARCHITECTURE.md`의 obs 마스킹 설명에 같은 규칙을 한 줄 쓴다.

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

## 금지사항

- 형식이 정해진 식별자 속성에 PII 정규식을 돌려 정상 값을 가리지 마라(FP-010). 이유: 무작위 hex에서 숫자 연속이 전화번호·카드번호 규칙에 걸려 값이 `[PII:mobile]` 등으로 바뀌고 trace 연결이 끊긴다. 형식 검증이 PII 검사를 대신한다. 단 **키와 형식이 모두 맞을 때만** 건너뛴다. 키만 맞거나 형식만 맞는 값은 검사한다.
- 마스킹을 약화하지 마라. 이유: 식별자 예외는 표에 있는 키 + 형식 일치에 한정한다. 표에 없는 키·형식 밖 값·민감 키·비밀 값 제거 동작은 그대로다. 예외를 넓히는 정규식(`.*_id` 같은 접미사 일치)을 쓰지 마라.
- `gateway/client.py`의 `generation_id` 생성(`secrets.token_hex(8)`)이나 `tests/test_gateway_client.py`의 테스트 입력을 바꾸지 마라(FP-011). 이유: 결함은 생성이 아니라 마스킹이다. 입력을 고정해 가리면 서비스에서 계속 무작위로 실패한다.
- 위 "수정할 파일" 목록 밖의 파일을 수정하지 마라. 목록 밖 수정이 필요해 보이면 임의로 고치지 말고 `blocked`로 보고한다(FP-054). 이유: step 범위 누락은 멈춰서 설계에서 고친다.
- 무작위·시간·ID를 `monkeypatch`로 고정해 간헐적 실패를 덮지 마라(FP-011). 이유: 무작위 입력에서만 나타나는 실패는 실제 결함이다. 고정이 필요하면 사유 주석을 달고 시드 고정 대량 테스트를 함께 둔다.
- 외부 서비스(A MCP·LiteLLM·mock)의 응답 형태를 추측으로 만들어 그 추측만 검증하지 마라(FP-020). 이유: 틀린 추측은 틀린 테스트로 통과한다. 이 step이 의존하는 실측 결과(읽기 목록의 외부 계약 실측)를 픽스처로 쓰고, 실측 값이 없는 항목은 단정하지 말고 `blocked`로 보고한다.
- 이 파일의 지시와 AGENTS.md 계층 경계가 충돌하거나 이 step의 작업·금지사항이 서로 모순되면 임의로 판단하지 말고 `blocked`로 보고하라(FP-054). 이유: 모순을 임의로 해소하면 계층 경계가 깨진다.
- 이 step이 필요로 해서 테스트를 바꿀 때는 변경 목록(파일·테스트 이름·이유)을 최종 보고에 적어라(FP-056). 이유: 완화된 단언이 다음 step에서 방치된다. 삭제는 그 테스트가 검증하던 대상 자체가 이 step에서 제거된 경우만 허용한다.
- `.claude/`(skills·commands)와 `docs/results/`의 사전 등록 파일(`hypotheses.md`·`hypotheses-m3.md`·`hypotheses-m3-grading.md`·`hypotheses-m3-prompt.md`·`n-registration-m3.md`·`m3-dev-supersedes.json`·`m3-dev2-supersedes.json`·`2026-10-06-m3-dev-live.md`·`2026-10-06-m3-dev2-live.md` 등)은 읽기만 하고 수정하지 않는다(이 step이 명시적으로 허용한 경우 제외). 이유: 읽기 목록의 파일을 지시 없이 고치지 않게 한다(FP-055).
- `scripts/`, `phases/`, `web/`, `data/contract/`, `data/eval/`을 수정하지 마라(이 step이 명시적으로 허용한 경우 제외).
- `.env`와 A의 `data/interim/mcp/tokens.json`을 열거나 출력하지 마라. 토큰·키 값을 코드·테스트·문서·로그에 남기지 마라.
- A 저장소(`~/PycharmProjects/ko-docs-agentic-rag`)를 수정하지 마라(읽기 전용으로 참고하는 것만 허용).
- 실제 LiteLLM·OpenAI·Anthropic·Gemini·A MCP·Langfuse·Docker에 접속하는 테스트나 코드 실행을 하지 마라(로컬 루프백 서버와 합성 토큰은 허용). 이유: `make verify`는 키·네트워크 없이 통과해야 한다.
- `data/results/`의 실행 결과 디렉토리와 `data/replay/llm/`·`data/replay/a_run/`·`data/replay/golden/`의 기록 파일을 수정하지 마라(읽기만). 이유: 실행 기록과 골든 기록은 증거이며 live 러너가 만든다.
- 기존 테스트를 약화하지 마라(단언 삭제·`require_all=False`·슬라이스 비교·허용 오차 확대). 이름·시그니처 변경 때문에 필요한 호출·기대값 수정만 허용한다(FP-056). 계층 가드(`tests/test_architecture.py`)를 약화하지 마라.
