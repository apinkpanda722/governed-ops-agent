# Step 3: faults-client

## 읽어야 할 파일

- `/AGENTS.md`, `/docs/ADR.md`(ADR-018·019·020·021), `/docs/EVAL.md`, `/docs/THREAT_MODEL.md`("F1~F11" 표), `/docs/results/hypotheses.md`, `/docs/results/hypotheses-m3.md`, `/docs/results/2026-10-06-m3-dev2-live.md`, `/.claude/skills/failure-patterns/SKILL.md`
- `/src/govops/evalrun/mock_admin.py`(`MockAdmin`: `reset`·`state`·`faults_clear`·`faults_stats`·`health`; **`faults_set` 없음**, `Runner` 프로토콜에 stdin 입력 없음), `/src/govops/mocks/admin.py`(CLI: `faults set --file`), `/src/govops/mocks/faults.py`(`Rule`·`Action`·`ENDPOINTS`·규칙 JSON 형식), `/src/govops/evalrun/driver.py`, `/src/govops/evalrun/dev_m3.py`(실행 단위 흐름·`ToolPolicy` 조립·서킷), `/src/govops/tools/effects.py`·`/src/govops/tools/rest.py`·`/src/govops/tools/resilience.py`, `/src/govops/gateway/`(`ModelFaultScript`·`down_alias`·`GOVOPS_MODEL_FAULT_MODE`), `/tests/test_evalrun_mock_admin.py`, `/tests/test_effects_matrix.py`, `/tests/test_gateway_model_faults.py`(있다면)
- **외부 계약 실측**(2026-10-06): `docker compose exec -T <mock> python -m govops.mocks.admin faults set --file /dev/stdin`에 JSON 목록을 파이프하면 exit 0이고 `faults stats`·`faults clear`가 정상이다(로컬 admin 앱 + 서브프로세스로 확인). 규칙 형식은 `[{"endpoint","nth","action":{"type","when","status"|"ms"…}}]`. **mock 코드·이미지 변경은 필요 없다.** F4(연결 수립 실패)는 서버에서 순번별로 만들 수 없어(포트를 닫으면 순번 제어 불가) **클라이언트 전송 계층 주입**으로 한다. LiteLLM `down_alias`(닫힌 포트 배포 + fallbacks)의 호출 지연은 **미실측**이다(`smoke-faults` step에서 사용자가 측정).

## 작업

**배경**: 장애 하네스(다음 step)가 쓸 부품을 만든다. 하네스·시나리오 상수는 이 step에서 만들지 않는다. TDD.

### 1. `MockAdmin.faults_set` (`evalrun/mock_admin.py`)

- `Runner` 프로토콜에 선택적 입력(`input: str | None = None`)을 추가하고 `subprocess_runner`는 `subprocess.run(..., input=...)`로 전달한다. 기존 호출·가짜 러너 테스트는 수정 없이 통과해야 한다(기본값 `None`).
- `faults_set(service, rules)`: 규칙 목록을 JSON으로 직렬화해 **stdin으로** `faults set --file /dev/stdin`에 보낸다(compose exec은 `-T`가 이미 있다). `service`는 `mock-jira`·`mock-slack` 중 하나다. 규칙은 `mocks/faults.py`의 `Rule`·`Action` 검증을 통과한 것만 보낸다(알 수 없는 엔드포인트·종류는 `MockAdminError`가 아니라 보내기 전 `ValueError`).
- 실패는 기존 규칙대로 출력 없이 `MockAdminError("command_failed")`다(출력·예외 컨텍스트에 stdout을 싣지 않는다).

### 2. 클라이언트 쪽 장애 전송 계층 (`tools/` 안에 작은 모듈, 예: `tools/fault_transport.py`)

- 순번 스크립트로 mock REST 클라이언트(`tools/rest.py`가 쓰는 httpx 전송 계층)에 장애를 주입하는 `httpx.AsyncBaseTransport` 래퍼. 종류:
  - **F4 연결 수립 실패**: n번째 요청에서 **요청을 서버로 보내지 않고** `httpx.ConnectError`를 일으킨다(서버 요청 수가 늘지 않는다).
  - F5 보조(필요하면): 서버가 처리한 뒤 응답을 버리는 것은 mock 서버 쪽 규칙(`faults_set`)이 담당한다. 이 래퍼는 F4에만 필수다.
- 주입 순번·횟수는 생성자 인자다(전역 상태 금지). 호출 기록(요청 수·주입 수)을 읽는 속성을 둔다.
- `tools/`의 금지 import(`agent`·`api`·`eval`·`mocks`·`langgraph`·LLM SDK)를 지킨다.

### 3. 모델 장애 스크립트 연결 (F6, `down_alias`)

- 이미 `gateway/`에 있는 `ModelFaultScript`와 `down_alias` 백엔드(`GOVOPS_MODEL_FAULT_MODE`가 서비스 시작을 막는 규칙 포함)를 **하네스가 호출 단위로 쓸 수 있는 형태**로 이어 준다: 변형 `first`(첫 호출만 실패)·`second`(둘째 호출만 실패)·`all`(전부 실패)을 `ModelFaultScript`로 만들고, 호출 번호별로 `down_alias`(주 모델 대신 닫힌 포트 배포 `agent-primary-down`) 별칭을 고르는 어댑터를 둔다. `mock_param` 백엔드는 제거되어 있다(다시 만들지 않는다). 기존 `gateway`·`ModelFaultScript`의 공개 동작은 바꾸지 않는다. 어디에 어댑터를 두어야 `evalrun`·`gateway` 계층 경계를 지키는지 `tests/test_architecture.py`를 보고 정한다(`evalrun`은 LLM SDK·`broker` import 금지, `gateway`는 `agent`·`tools`·`broker`·`api` import 금지). 경계가 충돌하면 `blocked`로 보고한다(FP-054).

### 4. 실행마다 서킷 초기화

- 하네스가 실행마다 **새 `ToolPolicy`**(서킷 상태 포함)를 만들 수 있게 `dev_m3.py`의 조립 지점을 재사용 가능한 함수로 노출한다(동작 변경 없음, 기존 dev_m3 테스트는 수정 없이 통과). 새 `ToolPolicy` 두 개가 서킷 상태를 공유하지 않는다는 테스트를 둔다(앞 실행이 서킷을 열어도 다음 실행의 첫 호출이 서버에 닿는다).

### 5. 테스트

- `faults_set`: 가짜 러너가 받은 명령(`docker compose ... exec -T mock-jira python -m govops.mocks.admin faults set --file /dev/stdin`)과 stdin 입력(JSON)을 단언, 잘못된 규칙은 보내기 전 `ValueError`, 러너 실패는 `command_failed`로 출력 비노출.
- 장애 전송 계층: F4 n번째 요청만 `ConnectError`·서버 미도달, 그 외 요청은 통과, 주입 수 기록. 재시도 정책이 F4를 **표식 조회 없이** 바로 재시도(`classify=CONNECT`)하는 경로를 로컬 루프백 서버(요청 수 센다)로 단언한다(FP-005: 장애가 시험하려는 경로를 타는 증거).
- F6 어댑터: 세 변형이 호출 번호별로 기대한 별칭 순서를 만든다(가짜 gateway 클라이언트로).

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

- mock 코드(`src/govops/mocks/`)·compose 파일·`litellm/` 설정을 수정하지 마라. 이유: 실측대로 mock 변경이 필요 없다. 변경하면 사용자가 이미지를 재빌드해야 하고(FP-052) 이미 확인한 `stale_mock_image` 가드와 어긋난다.
- mock 관리 엔드포인트를 compose 내부 네트워크 밖으로 열지 마라(AGENTS.md CRITICAL). `faults set`은 `docker compose exec`로만 호출한다.
- 호스트에서 mock 관리 포트에 직접 HTTP로 접속하는 경로를 만들지 마라. 이유: 관리 엔드포인트는 내부 전용이다.
- F11 경로(chaos A 서버·클라이언트·하네스)에서 `KODOCS_MCP_TOKENS_FILE`과 실제 A 사용자 토큰을 읽거나 보내지 마라. 합성 토큰만 쓴다. chaos 서버는 요청 헤더(특히 `Authorization`)를 기록·출력·저장하지 않는다. 이유: 장애를 주입하는 서버는 외부 프로세스 역할이고 실제 자격 증명이 닿으면 안 된다(AGENTS.md CRITICAL: 토큰을 로그·trace에 남기지 않는다). 테스트로 강제한다.
- 주입한 장애가 시험하려는 경로를 타지 않는 시나리오를 만들지 마라(FP-005). 이유: 장애가 방어 로직을 실행시키지 않으면 중복 0이 아무 일도 안 일어나서 저절로 나온다. 시나리오마다 그 경로가 실제로 실행됐다는 증거(조회 호출 수 > 0, 대기 발생, 재시도 횟수)를 단언한다. F10은 항상 F5와 결합한다.
- 무작위·시간·ID를 `monkeypatch`로 고정해 간헐적 실패를 덮지 마라(FP-011). 이유: 무작위 입력에서만 나타나는 실패는 실제 결함이다. 고정이 필요하면 사유 주석을 달고 시드 고정 대량 테스트를 함께 둔다.
- 동시 실행 결과의 순서를 단언하지 마라(FP-012). 이유: 동시·연속 호출의 완료 순서는 비결정적이다. 집합·건수로 단언한다.
- 외부 서비스(A MCP·LiteLLM·mock)의 응답 형태를 추측으로 만들어 그 추측만 검증하지 마라(FP-020). 이유: 틀린 추측은 틀린 테스트로 통과한다. 이 step이 의존하는 실측 결과(읽기 목록의 외부 계약 실측)를 픽스처로 쓰고, 실측 값이 없는 항목은 단정하지 말고 `blocked`로 보고한다.
- git 이력·시간·I/O 지연에 의존하는 테스트를 로컬 통과만으로 끝내지 마라(FP-032). 이유: CI(얕은 복제·느린 디스크)에서 달라진다. 가상 시간 폴링은 `real_tick`처럼 실제로 조금 기다리고, 느린 I/O를 주입해 재현한 뒤 고친다.
- 이 step이 의존하는 외부 지점을 실측 없이 가정하지 마라(FP-050). 이유: 승인 뒤 live에서 결함이 드러나 수정이 늘었다. 읽기 목록의 실측 근거를 따르고 다른 동작을 관측하면 가정하지 말고 보고한다.
- 이 파일의 지시와 AGENTS.md 계층 경계가 충돌하거나 이 step의 작업·금지사항이 서로 모순되면 임의로 판단하지 말고 `blocked`로 보고하라(FP-054). 이유: 모순을 임의로 해소하면 계층 경계가 깨진다.
- 이 step이 필요로 해서 테스트를 바꿀 때는 변경 목록(파일·테스트 이름·이유)을 최종 보고에 적어라(FP-056). 이유: 완화된 단언이 다음 step에서 방치된다. 삭제는 그 테스트가 검증하던 대상 자체가 이 step에서 제거된 경우만 허용한다.
- `.claude/`(skills·commands)와 `docs/results/`의 사전 등록 파일(`hypotheses.md`·`hypotheses-m3.md`·`hypotheses-m3-grading.md`·`hypotheses-m3-prompt.md`·`n-registration-m3.md`·`m3-dev-supersedes.json`·`m3-dev2-supersedes.json`·`2026-10-06-m3-dev-live.md`·`2026-10-06-m3-dev2-live.md` 등)은 읽기만 하고 수정하지 않는다(이 step이 명시적으로 허용한 경우 제외). 이유: 읽기 목록의 파일을 지시 없이 고치지 않게 한다(FP-055).
- `scripts/`, `phases/`, `web/`, `data/contract/`, `data/eval/`을 수정하지 마라(이 step이 명시적으로 허용한 경우 제외).
- `.env`와 A의 `data/interim/mcp/tokens.json`을 열거나 출력하지 마라. 토큰·키 값을 코드·테스트·문서·로그에 남기지 마라.
- A 저장소(`~/PycharmProjects/ko-docs-agentic-rag`)를 수정하지 마라(읽기 전용으로 참고하는 것만 허용).
- 실제 LiteLLM·OpenAI·Anthropic·Gemini·A MCP·Langfuse·Docker에 접속하는 테스트나 코드 실행을 하지 마라(로컬 루프백 서버와 합성 토큰은 허용). 이유: `make verify`는 키·네트워크 없이 통과해야 한다.
- `data/results/`의 실행 결과 디렉토리와 `data/replay/llm/`·`data/replay/a_run/`·`data/replay/golden/`의 기록 파일을 수정하지 마라(읽기만). 이유: 실행 기록과 골든 기록은 증거이며 live 러너가 만든다.
- 기존 테스트를 약화하지 마라(단언 삭제·`require_all=False`·슬라이스 비교·허용 오차 확대). 이름·시그니처 변경 때문에 필요한 호출·기대값 수정만 허용한다(FP-056). 계층 가드(`tests/test_architecture.py`)를 약화하지 마라.
