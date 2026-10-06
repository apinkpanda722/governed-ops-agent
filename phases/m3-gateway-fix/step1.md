# Step 1: agent-key-models

## 읽어야 할 파일

- `/AGENTS.md`, `/docs/ADR.md` (ADR-007·019), `/docs/ARCHITECTURE.md` ("게이트웨이와 Guardrail 비대칭 구조"), `/docs/THREAT_MODEL.md` (F6)
- 직전 phase(`m3-gateway`) 산출물: `/src/govops/gateway/client.py`, `/src/govops/gateway/faults.py`, `/src/govops/gateway/smoke.py`, `/src/govops/gateway/agent_key.py`, `/src/govops/costs.py`, `/infra/litellm/config.yaml`, `/infra/litellm/config.obs.yaml`, `/src/govops/replay/llm.py`
- `/Makefile`(`agent-key` 타깃), `/tests/test_agent_key.py`, `/.env.example`

## 작업

**결함**: 에이전트 가상 키가 `models=["agent-primary"]`만 허용한다(`gateway/agent_key.py`의 `/key/generate` 본문). 장애 주입 별칭 `agent-primary-down`뿐 아니라 **운영 경로의 `agent-primary-only`(Anthropic 상한 소진 시)와 직접 호출되는 fallback 별칭도 같은 403**이 난다. 허용 목록을 고치고 기존 키를 갱신하는 사용자 실행 타깃을 만든다. TDD로 테스트를 먼저 쓴다.

- 코드 상수 `AGENT_KEY_MODELS = ("agent-primary", "agent-primary-only", "agent-primary-down", "agent-fallback")`를 `gateway/agent_key.py`에 두고 `/key/generate` 본문의 `models`에 쓴다.
- **일관성 테스트**: `AGENT_KEY_MODELS`의 집합이 `infra/litellm/config.yaml`과 `config.obs.yaml` 각각의 `model_list[*].model_name` 집합과 **같다**. 어느 한쪽에 별칭이 추가·삭제되면 테스트가 실패한다.
- **`make agent-key-update`** (사용자 실행): 이미 `.env`에 있는 `LITELLM_AGENT_KEY`의 허용 모델을 LiteLLM `POST /key/update`(`{"key": <기존 키>, "models": AGENT_KEY_MODELS}`, 마스터 키로 인증)로 갱신한다. **`.env`의 키 값은 바꾸지 않는다**(파일을 다시 쓰지 않는다). 기존 `agent-key` 명령의 CLI 구조(`--env-file`·`--base-url`·마스터 키 읽기·비밀 비노출)를 따르고 하위 명령 또는 플래그(`--update`)로 구현한다. `LITELLM_AGENT_KEY`가 비어 있으면 실패 메시지(키 없음)를 낸다. 출력은 `PASS agent-key-update models=<별칭 목록>` 또는 `FAIL <이유 코드>` 한 줄이며 키 값·마스터 키·응답 본문을 출력하지 않는다. `Makefile`에 `agent-key-update` 타깃(`.PHONY`, 주석: "compose LiteLLM 기동·`.env` 키 필요, 사용자 실행, 기존 키의 허용 모델 갱신")을 추가한다. `make verify`에는 넣지 않는다.
- 테스트(가짜 transport): 갱신 요청 본문이 위 형식이고 마스터 키가 `Authorization`에 쓰이며, `.env` 파일 내용이 바이트 단위로 변하지 않는다. 키 없음·HTTP 오류·연결 실패 시 비밀이 출력에 없다. 기존 `agent-key` 테스트는 불변이다.

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

- 갱신 때 `.env`를 다시 쓰거나 키를 새로 발급하지 마라. 이유: 기존 키를 유지한 채 허용 목록만 바꾼다.
- 키·마스터 키·응답 본문을 출력하거나 로그에 남기지 마라.
- 허용 목록을 `["*"]` 같은 전체 허용으로 만들지 마라. 이유: 최소 권한이다.
- `scripts/`, `phases/`, `web/`, `data/contract/`, `data/eval/`을 수정하지 마라(이 step이 명시적으로 허용한 경우 제외).
- `.env`와 A의 `data/interim/mcp/tokens.json`을 열거나 출력하지 마라. 토큰·키 값을 코드·테스트·문서·로그에 남기지 마라.
- A 저장소(`~/PycharmProjects/ko-docs-agentic-rag`)를 수정하지 마라.
- 실제 LiteLLM·OpenAI·Anthropic·Gemini·A MCP·Langfuse에 접속하는 테스트를 만들지 마라. 이유: `make verify`는 키·네트워크 없이 통과해야 한다.
- 기존 테스트를 깨뜨리지 마라. 계층 가드(`tests/test_architecture.py`)를 약화하지 마라.
