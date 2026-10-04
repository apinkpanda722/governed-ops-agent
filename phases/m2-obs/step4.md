# Step 4: obs-smoke-docs

## 읽어야 할 파일

먼저 아래 파일들을 읽고 프로젝트의 아키텍처와 설계 의도를 파악하라:

- `/AGENTS.md` ("명령어" 절, `CRITICAL:` 줄 개수), `/docs/ARCHITECTURE.md` ("trace 구조", "디렉토리 구조"), `/docs/ADR.md` (ADR-009), `/docs/THREAT_MODEL.md` (TH4, V4), `/docs/EVAL.md` ("운영 품질 검수", "비용 상한")
- 이전 step 산출물: `/src/govops/obs/`(`trace.py`·`mask.py`·`jsonl.py`·`langfuse.py`), `/src/govops/guardrails/pii.py`, `/src/govops/api/wiring.py`, `/src/govops/gateway/client.py`, `/src/govops/costs.py`(`report`), `/infra/litellm/config.obs.yaml`, `/infra/docker-compose.yml`, `/Makefile`(`obs-up`·`obs-down`·`cost-report`)
- 사용자 스모크 패턴: `/src/govops/api/smoke.py`(`smoke_agent` 등 기대값 상수·`SmokeResult`·CLI), `/tests/test_api_smoke.py`, `/docs/results/2026-10-04-m1-agent-live.md`(live 결과 문서 형식)
- `/tests/test_docs_consistency.py`, `/tests/test_makefile.py`

## 작업

(1) Langfuse 연동을 사용자가 live로 확인하는 **`make smoke-obs`**(기대값은 실행 전에 코드에 고정), (2) 이 phase가 만든 것의 **문서 동기화**를 한다. TDD로 테스트를 먼저 쓴다. 이 step에서도 실제 API·docker는 실행하지 않는다.

### 1. `smoke-obs` — `src/govops/api/smoke.py`에 `obs` 서브커맨드 추가

`python -m govops.api.smoke obs` (기존 `checkpointer`·`agent` 옆). 사용자가 compose 핵심 스택 + obs 프로필(`make obs-up`)과 live A MCP를 띄우고 `.env`에 Langfuse 키를 넣은 뒤 실행한다. 한 task를 실제로 실행하고 Langfuse API로 trace를 조회한다.

- **입력**: 사용자 `demo-platform-g3`, 요청 문장은 기존 `smoke_agent`의 `REQUEST`와 같은 장애 요약 요청에 **마스킹 프로브**를 덧붙인다(형식에 맞는 가짜 휴대전화 번호 한 개를 코드에서 조립해 요청 끝에 "참고 연락처: …"로 붙인다. 이 번호는 합성 값이며 실제 번호가 아니다). 실행은 기존 `open_task_service` 경로(실제 API·그래프·게이트웨이)를 쓴다.
- **trace 조회**: `GET {LANGFUSE_HOST}/api/public/traces/{task_id}`에 HTTP Basic(`LANGFUSE_PUBLIC_KEY`:`LANGFUSE_SECRET_KEY`)으로 접속한다(`httpx`. 키 값은 출력·예외에 남기지 않는다). Langfuse 수집은 비동기이므로 최대 60초까지 5초 간격으로 폴링한다.
- **기대값(코드 상수로 먼저 고정, 각 항목 PASS/FAIL 출력, 하나라도 FAIL이면 종료 코드 1)**:
  1. `E1_TRACE_EXISTS`: trace가 `task_id`로 조회된다.
  2. `E2_AGENT_SPANS`: observation 중 루트 `task`, `model_call` ≥ 1, `tool_call` ≥ 1이 있다(요청이 도구 조회를 요구한다).
  3. `E3_LITELLM_GENERATION`: LiteLLM이 만든 generation(타입 `GENERATION`) ≥ 1이 **같은 trace**에 있다(`existing_trace_id` 연결 성공).
  4. `E4_NO_MESSAGE_CONTENT`: 그 generation의 input·output이 비어 있다(`turn_off_message_logging`).
  5. `E5_PII_MASKED`: 마스킹 프로브의 가짜 번호가 Langfuse 응답 JSON 전체와 로컬 JSONL에 **없고**, 루트 span의 입력에 PII 자리 표시자가 있다.
  6. `E6_SECRETS_ABSENT`: `LITELLM_AGENT_KEY`·`LANGFUSE_SECRET_KEY`·`OPENAI_API_KEY`의 값이 Langfuse 응답 JSON과 로컬 JSONL에 없다(값 자체는 출력하지 않는다).
  7. `E7_COUNTS_MATCH`: 로컬 JSONL의 span 수와 Langfuse의 에이전트 span 수(LiteLLM generation 제외)가 같다.
  - **기록만(합격/불합격 아님)**: `INFO annotation_queues_status=<HTTP 상태 코드>` — `GET {LANGFUSE_HOST}/api/public/annotation-queues`의 응답 코드(ADR-009의 "annotation queue가 OSS에 포함되는지" 확인용). 요청이 실패하면 `INFO annotation_queues_status=unavailable`.
- 마지막에 사용자에게 "이제 `docker stats`로 Langfuse 포함 메모리를 확인해 결과 문서에 적는다"는 안내 한 줄을 출력한다(측정은 사용자가 한다).
- 로컬 JSONL 경로는 `JsonlSink` 기본 경로를 쓴다. 필요한 환경 변수가 없으면 어느 변수가 비었는지(이름만) 알려 주고 종료 코드 1이다.
- `Makefile`: `smoke-obs`(`uv run --env-file .env python -m govops.api.smoke obs`, 위에 "compose 핵심 스택 + `make obs-up` + live A + Langfuse 키 필요, 사용자 실행" 주석). `verify`가 호출하지 않는다. `.PHONY`·`tests/test_makefile.py` 반영(기존 `smoke-agent` 검사 방식).
- 테스트: 가짜 task 서비스·가짜 Langfuse HTTP 응답(`httpx.MockTransport`)·임시 JSONL로 각 기대값의 PASS 경로와 FAIL 경로(예: generation 없음, 번호가 응답에 새어 있음, 비밀 값 포함, span 수 불일치)를 검증한다. 폴링 시간 초과 시 `E1` FAIL. 출력과 예외에 키·가짜 번호 원문이 없다. annotation queue 응답 코드 200/404/연결 실패가 모두 `INFO`로만 출력된다.

### 2. 문서 동기화

- `/AGENTS.md` "명령어"에 사용자 실행 타깃 4개를 기존 형식으로 추가한다: `obs-up`, `obs-down`, `smoke-obs`, `cost-report`(각각 한 줄 설명). **`CRITICAL:` 줄은 추가·삭제·수정하지 않는다**(개수 25를 `tests/test_docs_consistency.py`가 검사한다).
- `/docs/ARCHITECTURE.md`:
  - "trace 구조": **LiteLLM → Langfuse trace ID 메타데이터 키를 `existing_trace_id`로 확정**했다고 적고(LiteLLM 공식 문서 기준, `v1.103.3`에서의 동작은 `make smoke-obs`로 확인한다는 단서 포함) "확인 필요" 표기를 이 항목에서 제거한다. annotation queue의 OSS 포함 여부는 여전히 "확인 필요(설치 직후 `smoke-obs`가 응답 코드를 기록)"로 둔다.
  - **마스킹 기준 표**를 추가한다: 요청·최종 답변·초안 = PII만 마스킹한 원문(8,000자 상한) / 도구 결과 = 문서·티켓 ID + 앞부분 300자 발췌(PII·비밀 마스킹) / 도구 인자 = PII·비밀 마스킹 / 토큰·Authorization·키 = 항상 제거 / 모델 호출 프롬프트·응답 원문 = span에 없음 + LiteLLM 콜백 `turn_off_message_logging`(마스킹 우회 경로 차단). 마스킹은 sink 직전 한 지점(`obs/mask.py`)이다.
  - 디렉토리 구조: `obs/`(trace·mask·jsonl·langfuse), `guardrails/pii.py`, `infra/litellm/config.obs.yaml`, `logs/traces/<task_id>.jsonl`을 반영한다. 호스트에서 Langfuse를 켜는 순서(`make obs-up` → UI에서 프로젝트·API 키 생성 → `.env` → `make obs-up` 재실행)도 한 줄로 적는다.
- `/docs/ADR.md` ADR-009 **본문**: 키 이름 확정(`existing_trace_id`)과 마스킹 기준(요청·답변·초안은 PII 마스킹 원문, 도구 결과는 ID+발췌, LiteLLM 콜백 원문 끔)을 반영한다. **ADR에 "변경 기록" 줄을 새로 추가하지 않는다**(본문만 고친다. 기존 변경 기록 줄은 건드리지 않는다).
- `/docs/THREAT_MODEL.md`: TH4/V4의 trace 검증 항목에 "trace 저장 파일 스캔 + `smoke-obs` E5·E6"을 한 줄로 반영한다(계획된 검증 계약 표기 규칙을 따른다. 실측 결과는 쓰지 않는다).
- `/docs/EVAL.md` "비용 상한" 절 끝에 `make cost-report`(추정 상한 대비 실제 비용 비율, 파일럿 전 참고용 재추정)를 한 줄로 적는다.
- `tests/test_docs_consistency.py`에 추가: AGENTS.md 명령어에 새 타깃 4개(`make <이름> ` 형식), `CRITICAL:` 25 유지, ARCHITECTURE에 `existing_trace_id`와 마스킹 기준 표의 핵심 문구(`PII만 마스킹한 원문`, `300자`, `turn_off_message_logging`), ADR-009 본문에 `existing_trace_id`와 `변경 기록` 아래 항목 수가 작업 전과 같음(작업 시작 시 파일에서 읽어 상수로 단언), ADR-009 본문에 "변경 기록"이라는 말이 없다.

## Acceptance Criteria

```bash
make verify   # ruff check + ruff format --check + pytest + web 검사
```

## 검증 절차

1. 위 AC 커맨드를 실행한다.
2. 체크리스트를 확인한다:
   - 스모크 기대값 7개와 INFO 1개가 코드 상수로 먼저 고정되어 있고 실제 API를 호출하는 테스트가 없는가?
   - 문서의 타깃·경로·키 이름이 실제 코드·Makefile과 일치하는가? `CRITICAL:` 줄 수가 25인가? ADR에 변경 기록 줄을 추가하지 않았는가?
3. 결과를 최종 응답(JSON)으로 보고한다. `phases/` 아래 파일은 수정하지 않고 git commit도 하지 않는다:
   - 성공 → `"status": "completed"`, `"summary": "산출물 한 줄 요약"`
   - 해결할 수 없는 실패 → `"status": "error"`, `"reason": "구체적 에러 내용"`
   - 사용자 개입 필요 → `"status": "blocked"`, `"reason": "구체적 사유"` 후 즉시 중단

## 금지사항

- `make smoke-obs`·`make obs-up`·`docker`를 실행하거나 Langfuse·LiteLLM·A에 접속하지 마라. 이유: 실제 호출·기동은 사용자가 한다.
- 스모크 결과를 보고 기대값을 바꾸지 마라. 이유: 기대값은 실행 전에 고정한다.
- 실측 수치(메모리·지연·비용)를 문서에 쓰지 마라. 이유: 사용자가 실행한 뒤 `docs/results/`에 기록한다.
- AGENTS.md의 `CRITICAL:` 줄을 추가·삭제·수정하지 마라. ADR에 "변경 기록" 줄을 추가하지 마라. 이유: 문서 일관성 테스트와 프로젝트 문서 규칙.
- 스모크의 가짜 번호·키 값을 출력·로그에 남기지 마라. 이유: 토큰·PII 비노출 규칙.
- `.env`와 A의 `data/interim/mcp/tokens.json`을 열거나 출력하지 마라. A 저장소를 수정하지 마라.
- `scripts/`, `phases/`, `web/`, `data/contract/`를 수정하지 마라.
- 기존 테스트를 깨뜨리지 마라.
