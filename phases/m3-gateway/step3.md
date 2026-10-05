# Step 3: model-faults

## 읽어야 할 파일

- `/AGENTS.md`, `/docs/ADR.md` (ADR-007·008·009·018), `/docs/ARCHITECTURE.md` ("게이트웨이와 Guardrail 비대칭 구조", "상태 관리"의 비용 원장), `/docs/THREAT_MODEL.md` (F6)
- `/src/govops/gateway/client.py`(이전 step 결과), `/infra/litellm/config.yaml`(`agent-primary-down`), `/src/govops/api/wiring.py`, `/src/govops/api/startup.py`, `/tests/test_gateway_client.py`, `/tests/test_api_wiring.py`, `/docs/THREAT_MODEL.md`(F6)

## 작업

**모델 장애(F6) 주입 장치**를 `gateway/`에 만든다. 하네스(다음 phase)가 쓴다. TDD로 테스트를 먼저 쓴다.

### 호출 번호 기준 스크립트 (`src/govops/gateway/faults.py`)

```python
class ModelFaultScript:
    def __init__(self, failing_calls: Collection[int] | Literal["all"]): ...
    def should_fail(self, task_id: str) -> bool        # task별 호출 번호(1부터)를 세어 판정, 호출마다 증가
def model_fault_mode() -> str | None                   # 환경 변수 GOVOPS_MODEL_FAULT_MODE: None | "mock_param" | "down_alias" (그 밖의 값은 ValueError)
```

변형(하네스가 쓴다): 첫 호출만 실패 = `{1}`, 둘째 호출만 실패 = `{2}`, 전부 실패 = `"all"`.

### 두 백엔드 (`LiteLLMChatClient`에 선택 인자 `fault_script`·`fault_mode`)

- **`mock_param`**: 실패해야 하는 호출의 요청 본문에 LiteLLM의 `mock_testing_fallbacks: true`를 넣는다(주 모델 호출이 실제 공급사에 가지 않고 실패한 것으로 처리되어 fallback으로 넘어간다). LiteLLM 공식 문서로 이 파라미터가 요청 본문(extra_body)으로 전달되는 방식을 확인한다(**확인 필요**: 프록시 v1.103.3에서 수용되는지는 사용자 실행 스모크가 확인한다).
- **`down_alias`**: 실패해야 하는 호출은 요청 모델을 `agent-primary-down`(닫힌 포트를 가리키는 배포, fallbacks로 `agent-fallback`에 연결)으로 바꿔 보낸다. 나머지 호출은 `agent-primary`.
- 스크립트가 없으면(`fault_script=None`) 운영 경로와 완전히 같다(본문·별칭 불변). 두 백엔드 모두 **원장 정산은 이전 step의 규칙(응답 모델 기준)을 그대로** 쓴다.
- **일반 서비스 보호**: `GOVOPS_MODEL_FAULT_MODE`가 설정돼 있으면(`model_fault_mode()`가 None이 아니면) `api/wiring.py`의 `open_task_service`(api 시작)는 `StartupError`로 **시작을 거부**한다. 장애 주입은 하네스 코드가 클라이언트에 `fault_script`를 직접 넘길 때만 가능하다(하네스는 `model_fault_mode()`로 백엔드를 고른다).
- chaos proxy·별도 프록시 프로세스를 만들지 않는다(키가 B 밖의 프로그램을 거치지 않게 한다).
- `.env.example`에 `GOVOPS_MODEL_FAULT_MODE` 이름과 한 줄 설명("하네스 전용, 일반 서비스에서는 비워 둔다")을 추가한다.

### 테스트 (가짜 HTTP)

- `ModelFaultScript`: 변형 3종의 호출 번호 판정, task별 카운터 독립, 호출마다 증가.
- `mock_param`: 실패 호출의 요청 본문에만 `mock_testing_fallbacks`가 들어가고 나머지 호출은 본문이 일반 호출과 동일하다. `down_alias`: 실패 호출만 모델이 `agent-primary-down`이다.
- 스크립트 없음 → 요청 본문·모델 별칭이 이전과 같다(기존 게이트웨이 테스트 불변).
- 환경 변수 검증(미설정·빈값·`mock_param`·`down_alias`·잘못된 값), `GOVOPS_MODEL_FAULT_MODE` 설정 시 `open_task_service`가 시작을 거부한다.

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
   - 사용자 개입 필요 → `"status": "blocked"`, `"reason": "구체적 사유"` 후 즉시 중단

## 금지사항

- 장애 주입 설정을 서비스가 시작되는 경로(환경 변수만으로)에서 활성화하지 마라. 이유: 일반 서비스에서 장애 주입이 켜지면 운영 호출이 의도치 않게 fallback으로 간다.
- 하네스용 장애 주입 호출도 비용 원장·상한 검사를 우회하게 만들지 마라.
- 실제 LiteLLM·공급사에 접속하는 테스트를 만들지 마라.
- 백엔드를 하나로 고정하지 마라. 이유: 어느 쪽이 쓰일지는 사용자 스모크(`smoke-fallback`) 결과로 사전 등록 때 정해진다.
- `scripts/`, `phases/`, `web/`, `data/contract/`, `data/eval/`을 수정하지 마라(이 step이 명시적으로 허용한 경우 제외).
- `.env`와 A의 `data/interim/mcp/tokens.json`을 열거나 출력하지 마라. 토큰·키 값을 코드·테스트·문서·로그에 남기지 마라.
- A 저장소(`~/PycharmProjects/ko-docs-agentic-rag`)를 수정하지 마라.
- 실제 LiteLLM·OpenAI·Anthropic·Gemini·A MCP·Langfuse에 접속하는 테스트를 만들지 마라. 이유: `make verify`는 키·네트워크 없이 통과해야 한다.
- 기존 테스트를 깨뜨리지 마라. 계층 가드(`tests/test_architecture.py`)를 약화하지 마라.
