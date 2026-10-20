# M8 회귀 추가 등록 1 (`hypotheses-m8-regression-addendum-1.md`)

M8 추가 측정 — 공식 M6 수치 아님

scope: "m8"

상태: 이 파일은 후보 설정을 바꾸는 코드와 후보 러너 재실행 전에 커밋한다(FP-043). 커밋 뒤에는 수정하지 않는다. 원 등록 [`hypotheses-m8-regression.md`](hypotheses-m8-regression.md)는 그대로 두며, 아래 §3이 대체하는 부분(원 등록 5절의 `agent-primary-reference` 서술)을 제외한 기준 6개·한도·고정 코드·비용 상한·사용자 결정 A·B·실행 절차는 모두 원 등록 그대로다.

## 1. 경위

- 사용자가 live로 `make m8-regress-smoke REGRESS_ARGS="--stage candidate"`를 실행했고 4신호가 모두 PASS였다: 응답 배포 ID `anthropic-claude-sonnet-5-5`, `attempted_fallbacks` 0, Anthropic 비용 0.0226센트($0.000226), OpenAI 비용 0. 이어 `make m8-regress-precheck REGRESS_ARGS="--stage candidate"`도 PASS였다.
- 이어 `make eval-run-dev-m6 CONFIG=v1 RUN=M8-CAND`를 실행했으나 러너 사전 점검에서 `litellm_config_stale`(2회)과 `service_down:mock-jira`·`service_down:mock-slack`으로 **task 실행 전에 종료**했다. 비용 0, 결과 디렉터리 없음. mock 서비스는 사용자가 따로 띄운다.
- **후보 실행의 결과(TCR·도구 호출·비용·지연)는 하나도 보지 않았다.** 이 변경은 결과를 본 뒤의 기준 변경이 아니다(FP-043 위반 아님).

## 2. 원인

- 러너 사전 점검(`preflight.py`)은 팀 에이전트 키로 `/model/info`를 조회해 `required_deployments()`가 키에 보이는 배포 ID에 포함되는지 확인한다.
- 에이전트 키의 허용 모델(`agent_key.py`의 `AGENT_KEY_MODELS`)은 `agent-primary`·`agent-primary-only`·`agent-primary-down`·`agent-fallback`·`guardrail-v2` 다섯 개뿐이다. 후보 설정에서 gpt 배포(`openai-gpt-6-luna`)를 담은 새 별칭 `agent-primary-reference`는 이 목록에 없어 키로 보이지 않는다. [추론: LiteLLM의 키 권한 필터, 실패 양상과 일치. 미실측]
- smoke는 `agent-primary`만 호출해 통과했다. 사전 점검 경로를 오프라인 테스트로 대조하지 않았다(FP-074·FP-076).

## 3. 변경 (하나뿐)

후보 설정에서 gpt 배포(`openai-gpt-6-luna`, `litellm_params`는 기준 `agent-primary` 그대로)의 `model_name`을 **`agent-primary-reference`에서 `agent-fallback`으로** 바꾼다.

- 후보 `model_name` 집합 = 기준 `config.yaml`의 집합 = `AGENT_KEY_MODELS`.
- `router_settings.fallbacks`는 `agent-primary-down: [agent-primary]` 그대로다. `agent-primary`의 fallback은 없으므로 `agent-fallback`(gpt 배포)은 호출되지 않는다. 호출되면 유효성 코드 `invalid_deployment_evidence`가 잡는다.
- 원 등록 5절의 `agent-primary-reference` 서술(표의 "기존 gpt 배포 → `agent-primary-reference`로 이동", "기존 `agent-fallback` 항목 제거")은 이 추가 등록으로 **대체**된다. 후보 `agent-primary`(Sonnet, 기준 `agent-fallback`의 `litellm_params` 그대로)는 변하지 않는다.
- 배포 ID 집합, 타임아웃(`agent-primary` 30·그 외 15·`guardrail-v2` 8), `num_retries` 0, `api_key` 환경 참조는 그대로다.
- 후보 설정 파일 digest(`CANDIDATE_CONFIG_SHA256`)는 새 파일 값으로 바뀌며 테스트와 smoke 코드 둘이 같은 값을 쓴다.

## 4. 비용·기록

- smoke 1회 비용은 $0.000226(Anthropic)이다. 실패한 러너 시도의 비용은 0이다. 이 시도는 후보 실행 횟수에 세지 않으며, 새 라벨 없이 `M8-CAND`로 다시 실행한다(task 실행 전 종료, 결과 없음).
- 단계 상한과 호출 전 검사식은 원 등록 §11 그대로다.

## 5. 결과 문서에 적을 결함

"후보 설정의 새 별칭이 에이전트 키 허용 모델 밖이라 러너 사전 점검(`/model/info`, 키 권한)에서 배포가 보이지 않음. smoke는 `agent-primary`만 호출해 통과했다. 사전 점검 경로를 오프라인 테스트로 대조하지 않았다(FP-074·FP-076)."

## 6. 실행 절차

원 등록 §12 그대로다(`m8-candidate-up`으로 새 후보 설정을 적용한 뒤 smoke·precheck·러너). 후보 설정이 바뀌었으므로 `m8-candidate-up`을 다시 실행해 컨테이너를 재생성한다.
