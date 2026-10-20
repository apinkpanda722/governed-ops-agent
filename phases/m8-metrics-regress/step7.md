# Step 7: regress-matrix-smoke

## 읽어야 할 파일

먼저 아래 파일들을 읽고 프로젝트의 아키텍처와 설계 의도를 파악하라:

- `/AGENTS.md`(계층 경계 표의 `regress/` 행, CRITICAL 규칙: 비용 상한·fail-closed·토큰 비노출)
- `/docs/M8_DESIGN_DRAFT.md`(2-4절, 4-1절 R-3 행, 6절 3·4·5번)
- `/docs/results/hypotheses-m8-regression.md`(**smoke 합격 조건·단계 상한·필요 잔여액·코드 목록·실행 절차의 원본**, 6·10·11·12번 절. 읽기만 한다)
- `/src/govops/gateway/smoke.py`(기존 `fallback` smoke: 팀 키·가드레일 digest·원장 델타·진단 출력 방식. **이 step이 따라 하는 선례**, 읽기만 한다), `/src/govops/gateway/client.py`(`LiteLLMChatClient.complete`, `ChatResult`, `GatewayError`), `/src/govops/gateway/team_keys.py`(`TeamKeyResolver`), `/src/govops/gateway/hook_digest.py`(`compute_digests`)
- `/src/govops/costs.py`(`CostLedger`, `SUPPLIER_CAPS_USD`, `cost_report`, `resolve_deployment`·`resolve_model`)
- `/src/govops/regress/compare.py`, `/tests/test_regress_compare.py`(앞 step 산출물. 합성 결과 디렉터리 도우미 `build_synthetic_run`)
- `/tests/test_m8_candidate_config.py`(`CANDIDATE_CONFIG_SHA256`)
- `/Makefile`, `/tests/test_makefile.py`(`M8_ENV_CONTRACT` 표, `test_every_docker_compose_command_passes_the_env_file`), `/tests/test_smoke_gateway.py`(기존 smoke 테스트 방식)
- `/.claude/skills/failure-patterns/SKILL.md` — 읽기만 한다

이전 step에서 만들어진 코드를 꼼꼼히 읽고, 설계 의도를 이해한 뒤 작업하라.

## 작업

**수정할 파일**(이 목록이 전부다): `src/govops/regress/smoke.py`, `tests/test_regress_matrix.py`, `Makefile`, `tests/test_makefile.py`. TDD.

열람 금지 경로: `data/interim/`, `data/eval/`, `data/redteam/`, `src/govops/guardrails/rules_v1.json`
수정 허용 경로: `src/govops/regress/smoke.py`, `tests/test_regress_matrix.py`, `Makefile`, `tests/test_makefile.py`

열람 금지의 범위: 이 금지는 **Codex 세션이 해당 경로의 파일을 직접 열람·출력·인용하는 것**에 대한 것이다. 이 step의 코드가 런타임에 그 경로를 다루는 것(예: 기본 원장 위치 `data/manifest/`(`CostLedger()` 기본값), 사용자 시드 `data/seed/users.json`(기존 smoke가 읽는 런타임 입력))은 대상이 아니다. 테스트가 `data/interim/`·`data/eval/`·`data/redteam/`의 실제 파일 **내용**을 읽는 것은 여전히 금지이며 테스트는 임시 디렉터리·합성 명세·합성 관측만 쓴다. 예외로 `data/results/`의 실행 디렉터리는 파일 이름과 JSON 키 이름을 확인하는 데 한해 열람할 수 있고, 응답 본문(`answer`·`effect_bodies`)이나 근거 본문을 출력·인용하지 않는다. trace의 `output.answer`·`tool.result` 속성 값도 같다. `src/govops/guardrails/rules_v1.json`은 내용을 열거나 출력하지 않는다. 이 제한은 이 step이 새로 작성하는 테스트에 한한 것이며, 기존 `tests/test_m7_freeze.py`가 `scripts/freeze_digest.py`로 해시를 계산하며 동결 경로 파일을 바이트로 읽는 것은 `make verify`의 일부이므로 허용한다. Codex 세션이 그 파일 내용을 출력·인용하는 것은 여전히 금지다.

**목적**: (1) PASS/FAIL 합성 행렬과 통제 실행 판정 경로를 `compare`의 단일 진입점으로 고정하고, (2) 실제 후보·통제 실행 전 호출 0회로 비용 단계 상한을 검사하는 `precheck`, (3) 실응답 1건으로 응답 배포 ID와 원장 방향을 확인하는 `smoke`, (4) 후보 설정 적용·복구 Make 타깃을 만든다. **이 step은 어떤 live 명령도 실행하지 않는다.**

### 1. 실측 형태 (FP-076, FP-020)

- 기존 `govops.gateway.smoke`(`make smoke-fallback`)가 선례: `TeamKeyResolver(users)`로 팀 키를, `compute_digests(root)`로 훅 digest를 쓰고 `LiteLLMChatClient(ledger=…, key_resolver=…, expected_digests=…, require_guardrail=True, http_client=…)`로 `complete(messages=[…], tools=[], task_id=uuid4().hex, user_id=…, max_output_tokens=16)` 1건을 보낸다. `ChatResult`는 `message`·`model`·`usage`·`cost_usd`·`fallback_used`·`unmapped_model`·`deployment_id`·`body_model`·`model_group`·`attempted_fallbacks`·`guardrail`을 갖는다. 응답 모델은 본문 `model`이 아니라 **헤더 `x-litellm-model-id`(`deployment_id`)** 로 정한다(FP-020).
- 정산 방향(`gateway/client.py` 정산 루프): 호출마다 OpenAI `agent_primary` 예약과 Anthropic `agent_fallback` 예약을 잡고, 응답 공급사가 아닌 쪽의 예약은 0으로 정산한다. 후보(Sonnet 응답)에서는 `agent_fallback`(Anthropic)에 비용이 정산되고 `agent_primary`(OpenAI)는 0이다. 통제(gpt 응답)는 반대다.
- 원장: `CostLedger(root=None)`의 `committed_usd(supplier)`(정산 누적 + 미정산 예약)와 `total_usd(supplier)`(정산 누적), `SUPPLIER_CAPS_USD = {"openai": 9.13, "anthropic": 11.47, "gemini": 10.0}`. 원장 파일이 없으면 `LedgerMissingError`.
- 환경: 팀 키는 `LITELLM_TEAM_KEY_<팀>` 환경 변수(값은 `.env`), `SMOKE_USER`(기본 `demo-platform-g3`, `env_or`), `LITELLM_BASE_URL`(기본 `http://127.0.0.1:4000`). 값은 어디에도 출력하지 않는다.
- 합성 결과 디렉터리 도우미: 앞 step(R-2)의 `tests/test_regress_compare.py`에 모듈 수준 함수 `build_synthetic_run`이 있다(없으면 이 step은 `blocked`로 보고한다). `tests/`는 패키지가 아니므로 `importlib.util.spec_from_file_location`으로 불러 쓴다.

### 2. `src/govops/regress/smoke.py`

```python
STAGE_NEEDS: dict[str, dict[str, Decimal]]   # 등록 문서 11번: smoke/candidate/control × openai/anthropic
CANDIDATE_CONFIG_SHA256: str                  # tests/test_m8_candidate_config.py 상수와 같은 값
def precheck(stage: str, *, ledger: CostLedger | None) -> list[str]: ...         # 실패 코드 목록(비면 통과)
def evaluate_smoke(result: ChatResult, before: Mapping, after: Mapping, *, stage: str) -> list[Check]: ...  # 순수
def main(argv=None, *, client_factory=None, ledger=None, transport=None) -> int: ...
```

- **`precheck --stage {smoke,candidate,control} [--ledger-root DIR]`**: `STAGE_NEEDS`(등록 문서 11번의 숫자 그대로: smoke OpenAI 0.02·Anthropic 0.46, candidate 0.02·1.86, control 0.12·0.36)를 공급사별 `headroom = Decimal(str(SUPPLIER_CAPS_USD[s])) - Decimal(str(ledger.committed_usd(s)))`와 비교한다. 모자라면 `FAIL m8_regress_stage_cap_exceeded supplier=<openai|anthropic>`와 종료 코드 1, 원장을 읽지 못하면 `m8_regress_ledger_unreadable`. 통과 줄은 `PASS precheck stage=… openai_headroom=… anthropic_headroom=…`(달러, 소수 6자리). `candidate` 단계(와 `smoke --stage candidate`)는 `infra/litellm/config.candidate.yaml`의 sha256이 `CANDIDATE_CONFIG_SHA256`과 같은지도 본다(`m8_regress_candidate_config_mismatch`). **환경 변수를 읽지 않는다**(오프라인).
- **`smoke --stage {candidate,control}`**: ① 환경: `os.environ.get`/`env_or`로 읽고 필요한 값(팀 키)이 없으면 `FAIL m8_regress_env_unset missing=<개수>`(변수 이름·값을 쓰지 않는다)와 종료 코드 2. `KeyError`로 죽지 않는다 ② `precheck --stage smoke`와 같은 함수를 먼저 실행하고 실패하면 **클라이언트를 만들지 않고** 종료한다(호출 0회) ③ 호출 1건(고정 합성 메시지, `max_output_tokens=16`, `task_id=uuid4().hex`, 유한 타임아웃) ④ 호출 전후 공급사별 `total_usd`를 읽어 델타 ⑤ `evaluate_smoke`로 **독립 신호 네 개**를 따로 판정해 한 줄씩 `PASS|FAIL <신호>`: 후보 = 응답 배포 ID `anthropic-claude-sonnet-5-5`이고 `attempted_fallbacks`가 0 또는 None(`m8_regress_route_mismatch`) / 미등록 모델 아님(`unmapped_model` false이고 `resolve_model`이 mapped, `m8_regress_unmapped_model`) / Anthropic 정산 증가 > 0 / OpenAI 정산 증가 = 0(둘 다 `m8_regress_ledger_direction_mismatch`). 통제 = `openai-gpt-6-luna`, OpenAI 증가 > 0, Anthropic 증가 = 0. 배포 ID가 None이면 ①은 FAIL이다. 진단 줄 `INFO model_id=… model_group=… attempted_fallbacks=… resolved=… unmapped=… cost_total_cents=…`(비밀이 아닌 값만; 기존 smoke의 `_identifier` 방식으로 비밀 패턴이 섞이면 `redacted`) ⑥ 호출 오류는 타임아웃이면 `m8_regress_smoke_call_timeout`, 그 밖에는 `m8_regress_smoke_call_failed`와 예외 **클래스 이름·상태 코드만**.
- 마지막 줄 `SUMMARY pass=N fail=N skip=N`. **SKIP이 없어야 한다**: 조건 때문에 건너뛰는 신호가 없고 skip은 항상 0이다(FP-073). 하나라도 FAIL이면 종료 코드 1.
- 모든 코드는 등록 문서 10번의 고정 코드 목록에 있는 것만 쓴다. `budget_or_price_block`을 쓰지 않는다.

### 3. Makefile·`tests/test_makefile.py`

```make
.PHONY: m8-regress-precheck m8-regress-smoke m8-candidate-up m8-candidate-down
# 호출 0회 비용 단계 검사, 사용자 실행(.env 불필요)
m8-regress-precheck:
	uv run python -m govops.regress.smoke precheck $(REGRESS_ARGS)
# live LiteLLM 소액 호출 1건, 사용자 실행
m8-regress-smoke:
	uv run --env-file .env python -m govops.regress.smoke smoke $(REGRESS_ARGS)
# 후보 설정으로 litellm 컨테이너 재생성, 사용자 실행
m8-candidate-up:
	LITELLM_CONFIG_FILE=./litellm/config.candidate.yaml docker compose --env-file .env -f infra/docker-compose.yml up -d --force-recreate --no-deps litellm
# 기본 설정으로 복구, 사용자 실행
m8-candidate-down:
	LITELLM_CONFIG_FILE=./litellm/config.yaml docker compose --env-file .env -f infra/docker-compose.yml up -d --force-recreate --no-deps litellm
```

`verify` 레시피에 넣지 않는다. `tests/test_makefile.py`(수정 허용: **`M8_ENV_CONTRACT` 표에 `"m8-regress-precheck": False`, `"m8-regress-smoke": True`, `"m8-candidate-up": True`, `"m8-candidate-down": True` 행 추가, 후보 설정 파일이 `./litellm/config.candidate.yaml`로 마운트됨을 확인하는 단언 추가에 한함. 기존 단언 약화 금지**).

### 4. 테스트 (`tests/test_regress_matrix.py`, 가짜 클라이언트·임시 원장·합성 입력만)

- **PASS/FAIL 행렬**: `build_synthetic_run`으로 기준선과 변형 실행을 만들어 `govops.regress.compare.main`으로 판정한다(다른 진입점 금지, FP-044). 행: ① 통제 = 기준선과 같은 관측·지연 오차 이내 → PASS ② 후보 정상(후보 모델·배포·`fallback_used` true) → PASS ③ 단독 실패 행 8개(`tcr_drop`·`forbidden_violation`·`toolcall_required_drop`·`toolcall_ok_drop`·`golden_structure`·`cost_over`·`latency_over`·`invalid_run`): 각각 정확히 해당 코드만 ④ 이중 실패(예: `tcr_drop`+`cost_over`)는 두 코드 모두 ⑤ 후보가 비용만 초과(기대되는 Sonnet 형태): `verdict`는 FAIL이고 `behavior_criteria_met`는 true, `cost_criterion_met`는 false ⑥ 통제가 FAIL이 되는 입력도 판정 형식이 같다(통제 실패를 후보 판정과 같은 코드로 보고).
- **precheck**: 임시 원장으로 잔여 충분 → 통과, Anthropic만 모자람(`candidate` 필요 1.86) → `m8_regress_stage_cap_exceeded supplier=anthropic`, OpenAI만 모자람(`control` 필요 0.12) → `supplier=openai`, 정확히 필요액과 같으면 통과·1센트 모자라면 실패(경계), 원장 없음 → `m8_regress_ledger_unreadable`, 후보 설정 파일 변조 → `m8_regress_candidate_config_mismatch`. 등록 문서 11번의 숫자(`STAGE_NEEDS`)와 코드가 일치함을 문서 파싱으로 대조한다.
- **호출 0회**: precheck가 실패하는 상태에서 `smoke`를 실행하면 `client_factory`가 한 번도 호출되지 않고 원장 줄 수가 변하지 않는다. 환경 변수가 없으면(`monkeypatch.delenv`) `FAIL m8_regress_env_unset`과 종료 코드 2이며 `KeyError`가 아니다. `precheck`는 빈 환경에서도 동작한다.
- **`evaluate_smoke`의 독립 신호(FP-021)**: 후보에서 네 신호가 각각 **단독으로** 실패하는 입력(배포 ID만 틀림, 미등록 모델만, Anthropic 증가가 0, OpenAI 증가가 0이 아님)에서 해당 코드만 나오고 나머지 세 신호는 PASS다. 통제도 같다. 배포 ID가 None이면 실패. 보수 정산이 두 공급사 모두를 올리는 상황(미등록 응답)은 방향 신호가 실패한다.
- **SKIP 없음**: 출력에 `SKIP`이 없고 `SUMMARY`의 skip이 0(FP-073).
- 출력(stdout·stderr)에 가짜 팀 키·`Authorization`·합성 응답 본문 표식이 없다. 호출 오류는 클래스 이름만.
- `CANDIDATE_CONFIG_SHA256`이 실제 `infra/litellm/config.candidate.yaml` 바이트의 sha256과 같다. 소스 AST에 `os.environ[`(대괄호 접근)가 없다.
- 코드 이름 문자열이 등록 문서 10번의 고정 코드 목록에 모두 있고, `compare.py`와 `smoke.py`의 `m8_regress_` 코드 합집합이 문서 목록(17개)과 정확히 같다.

### 5. 소비자 점검

`Makefile` 소비자 경고를 읽었다: 기존 타깃 레시피 정확 일치 단언(`tests/test_evalrun_dev_m3_m6.py`·`tests/test_evalrun_redteam_run.py`)과 `verify` 레시피 불변 단언(`tests/test_makefile.py`)을 건드리지 않는다. `docker compose` 줄은 `--env-file .env`를 모두 가져야 한다(`test_every_docker_compose_command_passes_the_env_file`). 위 파일들은 수정하지 않고 `make verify`로 통과만 확인한다.

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

- `make m8-regress-smoke`·`make m8-candidate-up`·`make m8-candidate-down`·`docker compose`를 실행하지 마라. 클라이언트가 실제 LiteLLM에 접속하는 테스트도 만들지 마라. 이유: 실제 서비스 접속은 사용자 실행 항목이다. 클라이언트는 `client_factory`·`transport` 주입으로 가짜를 쓴다(AGENTS.md).
- smoke의 합격을 한 신호(예: Anthropic 정산 증가 > 0)로 판정하지 마라. 이유: 미등록 응답을 두 공급사에 정산하는 보수 규칙 때문에 한 신호는 우연히 만족될 수 있다. 독립 신호 네 개가 모두 필요하다(FP-021).
- `os.environ["…"]`로 환경 변수를 읽거나, 환경 누락을 일반 예외로 가리지 마라. 이유: 오프라인 타깃에 DB·환경 의존을 넣다가 `KeyError`가 고정 실패 코드를 가린 선례가 있다(FP-076, #193·#197). 누락은 `m8_regress_env_unset`으로 끝낸다.
- 비용 상한 검사 전에 클라이언트를 만들거나 요청을 보내지 마라. 이유: 상한에 걸리면 호출 0회로 멈춰야 한다(AGENTS.md 비용 상한 규칙, FP-051).
- 호출·서브프로세스에 시간 제한 없는 대기를 두지 마라. 이유: CI 정지 선례(FP-066). 클라이언트 호출은 유한 타임아웃, 테스트는 가짜로 즉시 끝난다.

## 자기 점검표 (작성자 기입. 문제 신호: 첫째 질문 "예", 둘째 질문 "아니오", 셋째 질문 "예")

- 수정 파일 목록 밖의 변경이 필요한가: 아니오. 설계 4-1절 R-3 행에 `tests/test_makefile.py`를 더했다(Make 타깃 환경 계약 표가 이 파일에 있다).
- AC를 이 step 안에서 달성할 수 있는가: 예(`make verify`는 가짜 클라이언트·합성 입력만 쓴다). 단 smoke의 live 동작(응답 헤더가 실제로 후보 배포 ID인지, 에이전트 키가 `agent-primary`를 허용하는지)은 이 step 안에서 확인할 수 없고 사용자 실행 첫 smoke에서만 드러난다.
- 다른 step·등록 문서와 모순되는가: 아니오. 등록 문서 6·10·11·12번 절의 숫자·코드·타깃 이름과 같게 썼고 테스트가 대조한다. 한 가지: 등록 문서 12번은 `m8-regress-compare`·`m8-regress-precheck`·`m8-regress-smoke`·`m8-candidate-up`·`m8-candidate-down` 5개 타깃 이름을 쓰며 R-2와 이 step이 나눠 구현한다.
