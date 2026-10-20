# Step 5: candidate-config

## 읽어야 할 파일

먼저 아래 파일들을 읽고 프로젝트의 아키텍처와 설계 의도를 파악하라:

- `/AGENTS.md`, `/docs/ADR.md`(ADR-019 10항: 설정 파일과 에이전트 키 허용 모델의 일치)
- `/docs/results/hypotheses-m8-regression.md`(**이 step의 구조 규칙 원본**, 5번 절. 앞 step 산출물, 읽기만 한다)
- `/docs/M8_DESIGN_DRAFT.md`(0-4절, 1-2절, 2-4절)
- `/infra/litellm/config.yaml`(기준 설정, **읽기만 한다. 동결 digest 대상이다**), `/infra/litellm/config.obs.yaml`
- `/src/govops/evalrun/preflight.py`(`required_deployments`, `litellm_config_stale` 판정), `/src/govops/costs.py`(`DEPLOYMENT_MODELS`·`CLASSIFIER_DEPLOYMENTS`·`resolve_deployment`)
- `/tests/test_v2_alias_config.py`, `/tests/test_agent_key.py`, `/tests/test_compose.py`, `/tests/test_gateway_client.py`(설정 파일 이름을 **명시적으로** 나열하는 기존 소비자 테스트. 읽기만 한다)
- `/.claude/skills/failure-patterns/SKILL.md` — 읽기만 한다

## 작업

**수정할 파일**(이 목록이 전부다): `infra/litellm/config.candidate.yaml`, `tests/test_m8_candidate_config.py`. TDD(불변식 테스트를 먼저 쓰고 통과하는 설정을 쓴다).

열람 금지 경로: `data/interim/`, `data/eval/`, `data/redteam/`, `src/govops/guardrails/rules_v1.json`
수정 허용 경로: `infra/litellm/config.candidate.yaml`, `tests/test_m8_candidate_config.py`

열람 금지의 범위: 이 금지는 **Codex 세션이 해당 경로의 파일을 직접 열람·출력·인용하는 것**에 대한 것이다. 이 step의 코드가 런타임에 그 경로를 다루는 것(예: 없음, 이 step의 테스트가 읽는 파일은 `infra/litellm/config.yaml`·`config.candidate.yaml`과 등록 문서뿐이다)은 대상이 아니다. 테스트가 `data/interim/`·`data/eval/`·`data/redteam/`의 실제 파일 **내용**을 읽는 것은 여전히 금지이며 테스트는 임시 디렉터리·합성 명세·합성 관측만 쓴다. 예외로 `data/results/`의 실행 디렉터리는 파일 이름과 JSON 키 이름을 확인하는 데 한해 열람할 수 있고, 응답 본문(`answer`·`effect_bodies`)이나 근거 본문을 출력·인용하지 않는다. trace의 `output.answer`·`tool.result` 속성 값도 같다. `src/govops/guardrails/rules_v1.json`은 내용을 열거나 출력하지 않는다. 이 제한은 이 step이 새로 작성하는 테스트에 한한 것이며, 기존 `tests/test_m7_freeze.py`가 `scripts/freeze_digest.py`로 해시를 계산하며 동결 경로 파일을 바이트로 읽는 것은 `make verify`의 일부이므로 허용한다. Codex 세션이 그 파일 내용을 출력·인용하는 것은 여전히 금지다.

**목적**: 항목 5의 후보 설정을 만든다. `agent-primary` 별칭이 `claude-sonnet-5-5`에 응답하고, 배포 ID 집합은 `config.yaml`과 같아 `litellm_config_stale` 사전 점검을 통과한다. 이 step은 LiteLLM을 띄우지 않는다(로컬에 설치돼 있지 않다). 라우팅이 실제로 맞는지는 R-3의 smoke(사용자 실행)가 확인한다.

### 1. 설정 구조 (등록 문서 5번 절과 같아야 한다)

`config.yaml`의 항목은 `agent-primary`(id `openai-gpt-6-luna`), `agent-fallback`(id `anthropic-claude-sonnet-5-5`), `agent-primary-only`(id `openai-gpt-6-luna-only`), `agent-primary-down`(id `openai-gpt-6-luna-down`), `guardrail-v2`(id `anthropic-claude-haiku-5-5`)다. 같은 배포 ID를 두 `model_name`이 쓰면 안 되므로 후보 설정은 다음과 같다. `config.yaml`처럼 `api_key` 줄은 환경 변수 참조(`os.environ/<VAR>`)로만 쓴다. 비밀 문자열을 쓰지 않는다.

```yaml
model_list:
  - model_name: agent-primary            # 후보: claude-sonnet-5-5가 별칭을 맡는다
    model_info:
      id: anthropic-claude-sonnet-5-5
    litellm_params:
      model: anthropic/claude-sonnet-5-5
      api_key: os.environ/ANTHROPIC_API_KEY
      timeout: 30                        # 기준 config.yaml의 agent-fallback 항목 그대로(사용자 결정 A)
      num_retries: 0
  - model_name: agent-primary-reference  # 기존 gpt 배포를 그대로 보존해 배포 ID 집합을 유지한다(호출되지 않음)
    model_info:
      id: openai-gpt-6-luna
    litellm_params:
      model: openai/gpt-6-luna
      api_key: os.environ/OPENAI_API_KEY
      timeout: 15
      num_retries: 0
  # agent-primary-only, agent-primary-down, guardrail-v2: config.yaml 항목을 그대로 복사(순서 포함)
router_settings:
  fallbacks:
    - agent-primary-down: [agent-primary]
# general_settings, guardrails: config.yaml과 같다
```

- `agent-fallback` 항목은 없다(그 배포 ID를 `agent-primary`가 쓴다). `agent-primary`의 fallback은 두지 않아 후보의 실패가 기준 모델로 가려지지 않는다. `litellm_settings`(Langfuse 콜백)는 넣지 않는다(`config.yaml`과 같다).
- 위 YAML의 주석 줄은 파일에도 남겨 의도를 설명하되, 주석 이외의 내용은 `config.yaml`의 해당 항목과 키 이름·값 모두 같게 쓴다.

### 2. 테스트 (`tests/test_m8_candidate_config.py`)

`yaml.safe_load`로 기준과 후보를 읽어 비교한다. 모든 단언은 개수 검사가 아니라 **내용 대조**다.

- 배포 ID 집합이 같고(`{m["model_info"]["id"] for m in model_list}`), `govops.evalrun.preflight.required_deployments()`와 `required_deployments(v2=True)`가 후보의 ID 집합에 포함된다.
- `model_name → id` 대응: `agent-primary`→`anthropic-claude-sonnet-5-5`, `agent-primary-reference`→`openai-gpt-6-luna`, `agent-primary-only`·`agent-primary-down`·`guardrail-v2`는 기준과 같다. 후보 `model_name` 집합과 기준의 차이는 정확히 `agent-fallback`(제거)와 `agent-primary-reference`(추가)뿐이다.
- `agent-primary-reference`의 `litellm_params`·`model_info`가 기준 `agent-primary`와 같고, 후보 `agent-primary`의 `litellm_params` == 기준 `agent-fallback`의 `litellm_params`(timeout 30, `num_retries` 0; 사용자 결정 A: fallback이 없는 후보에서 15초 초과가 `invalid_run`으로 지연 회귀를 가리지 않게 한다). `timeout`은 `agent-primary` 30, `agent-primary-reference`·`agent-primary-down`·`agent-primary-only` 15, `guardrail-v2` 8, 모든 `num_retries`는 0.
- `router_settings` = `{"fallbacks": [{"agent-primary-down": ["agent-primary"]}]}`. `general_settings`·`guardrails`가 기준과 같고, 최상위 키 집합이 기준과 같으며 `litellm_settings`가 없다.
- 시크릿 없음: 모든 `api_key` 값은 `os.environ/` 참조이고, 항목별 `litellm_params`가 대응하는 기준 항목과 같다(`agent-primary` ↔ 기준 `agent-fallback`, `agent-primary-reference` ↔ 기준 `agent-primary`, 나머지는 같은 이름 항목). `master_key`·`database_url`도 `os.environ/` 참조. 파일 전체에 `sk-`·`Bearer ` 같은 키 패턴이 없다.
- 모든 배포 ID가 `govops.costs`의 `DEPLOYMENT_MODELS` 또는 `CLASSIFIER_DEPLOYMENTS`에 등록돼 있다(미등록이면 보수 정산·경보가 켜진다).
- `CANDIDATE_CONFIG_SHA256`: 테스트 안에 상수로 두고 `hashlib.sha256(파일 바이트)`와 같다고 단언한다. **상수는 최종 파일의 digest로 채운다**(파일을 고치면 상수도 같은 커밋에서 고친다). R-3의 사전 검사·smoke가 이 상수와 같은 값을 실행 때 대조한다.
- 등록 문서에 `agent-primary-reference`, `anthropic-claude-sonnet-5-5`, `openai-gpt-6-luna`가 모두 들어 있다(구조 규칙의 문서-설정 일치).
- 기존 설정 파일(`config.yaml`·`config.obs.yaml`)을 이 테스트가 수정하지 않는다(읽기만).

### 3. 소비자 점검

`tests/test_v2_alias_config.py`·`tests/test_agent_key.py`·`tests/test_compose.py`·`tests/test_gateway_client.py`는 설정 파일을 `config.yaml`·`config.obs.yaml` 이름으로 **명시적으로 나열**하고 `infra/litellm/`을 글롭하지 않는다(내가 `grep`으로 확인했다). 새 파일은 이 테스트들에 걸리지 않으므로 수정하지 않는다. 이 step의 변경이 `tests/test_m7_freeze.py`의 `infra/litellm/config.yaml` digest에 영향을 주지 않는지(기준 설정 파일 불변)만 `make verify`로 확인한다.

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

- `infra/litellm/config.yaml`·`config.obs.yaml`을 수정하지 마라. 이유: 동결 digest 대상이고 공식 구성 불변 증거다(설계 1-1절, `tests/test_m7_freeze.py`).
- 같은 `model_info.id`를 두 항목에 쓰지 마라. 이유: 배포 ID가 응답 모델을 정하는 신호(`x-litellm-model-id`)이고 `costs.resolve_deployment`가 이를 단가·공급사로 해석한다. 중복 ID는 라우팅과 정산을 모호하게 한다(FP-020).
- 후보 `agent-primary`에 fallback을 연결하거나 `litellm_settings`(Langfuse 콜백)를 넣지 마라. 이유: 후보의 실패가 기준 모델로 가려지면 회귀 감지가 무의미해지고, 콜백은 `config.yaml`과의 차이를 모델 교체 하나로 한정하는 원칙을 깬다.
- 후보 설정으로 LiteLLM을 기동하거나 `docker compose`를 실행하지 마라. 이유: 실제 서비스 접속은 사용자 실행 항목이다(AGENTS.md).
- 라우팅이 맞다고 단정하는 주석이나 문서를 쓰지 마라. 이유: 오프라인에서 검증하지 못했다. 확인 지점은 R-3 smoke의 응답 헤더다(FP-020, FP-050).

## 자기 점검표 (작성자 기입. 문제 신호: 첫째 질문 "예", 둘째 질문 "아니오", 셋째 질문 "예")

- 수정 파일 목록 밖의 변경이 필요한가: 아니오. 설계 4-1절 R-1 행에 있던 `tests/test_architecture.py`는 이 step에서 뺐다: `regress/` 디렉터리가 R-2에서 만들어지므로 R-1에서 계층 규칙 행을 더하면 `test_rules_cover_actual_layer_directories`가 실패한다(규칙은 있고 디렉터리는 없음). 규칙 행은 R-2로 옮겼다.
- AC를 이 step 안에서 달성할 수 있는가: 예.
- 다른 step·등록 문서와 모순되는가: 예. 설계 2-4절의 "`model_name` 집합 동일·fallbacks 동일"과 이 step의 구조(`agent-fallback` 제거, `agent-primary-reference` 추가, fallbacks 변경)는 다르다. 배포 ID 집합 유지(사전 점검 통과)와 한 배포 ID의 중복 금지를 동시에 만족하는 구조라서 R-0 등록 문서 5번에 같은 구조를 적었다. 또 후보 `agent-primary`의 `litellm_params`는 기준 `agent-fallback` 항목 그대로(timeout 30, 사용자 결정 A)이고 `api_key: os.environ/ANTHROPIC_API_KEY`를 포함한다(등록 문서 5번과 일치). 이 설정이 LiteLLM에서 실제로 `agent-primary`를 Sonnet으로 라우팅하는지는 미실측이다.
