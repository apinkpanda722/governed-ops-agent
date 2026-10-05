# Step 3: model-faults

## 읽어야 할 파일

- (선행 결함 수정용) `/src/govops/audit/log.py`, `/tests/test_audit_log*.py`(감사 로그 테스트들), `/tests/test_api_tasks.py`, `/tests/test_smoke_tools.py`, `/src/govops/tools/smoke.py`, `/src/govops/guardrails/pii.py`, 커밋 `4b53fb2`와 `023a86d`의 테스트 변경(`git show`로 확인)
- `/AGENTS.md`, `/docs/ADR.md` (ADR-007·008·009·018), `/docs/ARCHITECTURE.md` ("게이트웨이와 Guardrail 비대칭 구조", "상태 관리"의 비용 원장), `/docs/THREAT_MODEL.md` (F6)
- `/src/govops/gateway/client.py`(이전 step 결과), `/infra/litellm/config.yaml`(`agent-primary-down`), `/src/govops/api/wiring.py`, `/src/govops/api/startup.py`, `/tests/test_gateway_client.py`, `/tests/test_api_wiring.py`, `/docs/THREAT_MODEL.md`(F6)

## 작업

### 0. 선행: 감사 로그 식별자 검증 결함 수정

**결함**: `audit/log.py`의 `append`가 `task_id`·`actor_sub`에 개인정보 정규식(`find_pii`)을 돌린다. `task_id`는 `uuid4().hex`(32자리 hex)라 숫자가 길게 이어지면 전화번호 규칙에 걸린다. 무작위 10만 개를 실제 `find_pii`로 돌리면 약 0.453%가 거부된다. 걸린 task는 초안 감사 기록이 실패해 `audit_write_failed`로 끝난다(dev·장애 주입·서비스에서 무작위 실패). `m3-effects`에서 해시 필드에 대해 고친 것과 같은 패턴의 재발이다.

**수정** (테스트로 가리지 말고 코드를 고친다):
1. 식별자는 **형식으로 검증하고 PII 검사를 하지 않는다**. `task_id`는 `^[0-9a-f]{32}$`, `actor_sub`는 등록 사용자 라벨 형식 `^demo-[a-z-]+-g[0-9]+$`(또는 `data/seed/users.json`의 라벨 목록)이다. 형식 밖이면 `AuditWriteError`(`ValueError` 하위)로 거부한다. smoke·진단 코드가 쓰는 ID도 같은 형식을 쓴다(`smoke-` 같은 접두어 ID 금지, `uuid4().hex`를 쓴다). 감사 로그에 `task_id`·`actor_sub`를 넘기는 모든 호출 지점(`agent/`·`api/`·`tools/`·smoke)이 이 형식을 지키는지 확인하고, 지키지 않는 곳은 호출 쪽을 고친다.
2. **되돌릴 것**: 커밋 `4b53fb2`와 `023a86d`에서 무작위 `uuid4`를 고정 ID로 바꾸거나 `monkeypatch`한 테스트 변경(`tests/test_api_tasks.py`, smoke 관련 테스트 등)을 **되돌린다**. 테스트는 다시 무작위 ID로 돈다. 형식 변경 때문에 필요한 수정(예: 감사에 임의 문자열 `task_id`를 쓰던 기존 테스트를 32자리 hex로 바꾸는 것)은 허용하되, 입력을 고정해서 결함을 가리는 변경은 금지한다. 되돌린 목록을 최종 summary에 적는다.
3. **회귀 테스트**: ① 시드를 고정한 무작위 `uuid4().hex` 10,000개가 모두 감사 `append`를 통과한다(`task_id` 필드). ② 형식 밖 식별자는 거부된다: 전화번호·주민번호가 섞인 `task_id`, `demo-` 형식이 아닌 `actor_sub`, 길이·문자가 다른 hex. ③ 허용 형식이면 PII 정규식을 돌리지 않는다는 점을 위 ①이 보장한다.
4. 이 결함은 코드 수정이므로 ADR·문서 반영은 마지막 step(`gateway-docs`)이 한다.

### 1. 모델 장애 주입 장치

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

- 테스트에서 `uuid4`·난수를 고정(`monkeypatch`)해 무작위로 실패하는 결함을 가리지 마라. 이유: 무작위 입력에서만 나타나는 실패는 실제 결함이다(필요하면 사유를 주석으로 쓰고 같은 입력의 무작위 대량 테스트를 둔다).
- 장애 주입 설정을 서비스가 시작되는 경로(환경 변수만으로)에서 활성화하지 마라. 이유: 일반 서비스에서 장애 주입이 켜지면 운영 호출이 의도치 않게 fallback으로 간다.
- 하네스용 장애 주입 호출도 비용 원장·상한 검사를 우회하게 만들지 마라.
- 실제 LiteLLM·공급사에 접속하는 테스트를 만들지 마라.
- 백엔드를 하나로 고정하지 마라. 이유: 어느 쪽이 쓰일지는 사용자 스모크(`smoke-fallback`) 결과로 사전 등록 때 정해진다.
- `scripts/`, `phases/`, `web/`, `data/contract/`, `data/eval/`을 수정하지 마라(이 step이 명시적으로 허용한 경우 제외).
- `.env`와 A의 `data/interim/mcp/tokens.json`을 열거나 출력하지 마라. 토큰·키 값을 코드·테스트·문서·로그에 남기지 마라.
- A 저장소(`~/PycharmProjects/ko-docs-agentic-rag`)를 수정하지 마라.
- 실제 LiteLLM·OpenAI·Anthropic·Gemini·A MCP·Langfuse에 접속하는 테스트를 만들지 마라. 이유: `make verify`는 키·네트워크 없이 통과해야 한다.
- 기존 테스트를 깨뜨리지 마라. 계층 가드(`tests/test_architecture.py`)를 약화하지 마라.
