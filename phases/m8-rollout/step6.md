# Step 6: rollout-exercise

## 읽어야 할 파일

먼저 아래 파일들을 읽고 프로젝트의 아키텍처와 설계 의도를 파악하라:

- `/AGENTS.md`(CRITICAL: 실제 API 호출은 사용자가 실행, 실존 기관·사람 이름으로 합성 기록 금지)
- `/docs/M8_DESIGN_DRAFT.md`(2-3절, 6절 14행 "PASS 경로만 있는 시연 금지", 7절 "feature flag 점진 배포")
- `/docs/results/hypotheses-m8-rollout.md`(11·13·14절, 읽기만 한다)
- `/src/govops/api/routed.py`(step 3), `/src/govops/api/variant.py`(step 2), `/src/govops/rollout/policy.py`·`store.py`(step 1)
- `/src/govops/api/tasks.py`(`TaskService`, 읽기만 한다), `/src/govops/agent/graph.py`(`build_graph` 199행~, 읽기만 한다)
- `/src/govops/gateway/client.py`(`ClassifyResult` 106~111행, 읽기만 한다)
- `/tests/test_agent_graph.py`(`FakeChat`·`FakeTools`·`reply`: 가짜 chat이 돌려주는 값의 형태), `/tests/test_agent_semantic.py`(`Classifier` 36~53행)
- `/tests/test_api_routed.py`(step 3의 구성 방식)
- `/Makefile`(step 5가 더한 `m8-rollout-*` 타깃), `/tests/test_makefile.py`(`M8_ENV_CONTRACT`)
- `/.claude/skills/failure-patterns/SKILL.md` — 읽기만 한다

## 작업

**수정할 파일**(이 목록이 전부다): `src/govops/api/rollout_exercise.py`(신규), `tests/test_m8_rollout_exercise.py`(신규), `Makefile`(타깃 1개 추가에 한함), `tests/test_makefile.py`(`M8_ENV_CONTRACT`에 키 1개 추가와 새 테스트 함수 추가에 한함, 기존 단언 약화 금지), `AGENTS.md`(`## 명령어` 절에 줄 1개 추가에 한함), `tests/test_rollout_cli.py`(`EXCLUDED_TARGETS` 상수(14행)와 그 사용(162~164행)을 지우고, 문서에서 뽑은 타깃 집합이 정확히 `{m8-rollout-status, m8-rollout-set, m8-rollout-kill, m8-rollout-exercise}`라는 단언 한 줄로 바꾸는 것에 한함. 빈 제외 집합을 남기지 않는다(`EXCLUDED_TARGETS <= targets`가 공허하게 참이 된다, FP-069). 그 밖의 테스트·단언 불변). TDD.

열람 금지 경로: `data/interim/`, `data/eval/`, `data/redteam/`, `src/govops/guardrails/rules_v1.json`
수정 허용 경로: `src/govops/api/rollout_exercise.py`, `tests/test_m8_rollout_exercise.py`, `Makefile`, `tests/test_makefile.py`, `AGENTS.md`, `tests/test_rollout_cli.py`
열람 금지의 범위: 이 금지는 **Codex 세션이 해당 경로의 파일을 직접 열람·출력·인용하는 것**에 대한 것이다. 이 step의 코드는 `data/interim/`을 쓰지 않는다(시연은 자기 임시 디렉터리만 쓴다). 테스트가 `data/interim/`·`data/eval/`·`data/redteam/`의 실제 파일 **내용**을 읽는 것은 여전히 금지이며 테스트는 임시 디렉터리만 쓴다. 예외로 `data/results/`의 실행 디렉터리는 파일 이름과 JSON 키 이름을 확인하는 데 한해 열람할 수 있고, 응답 본문(`answer`·`effect_bodies`)이나 근거 본문을 출력·인용하지 않는다. 이 제한은 이 step이 새로 작성하는 테스트에 한한 것이며, 기존 `tests/test_m7_freeze.py`가 `scripts/freeze_digest.py`로 해시를 계산하며 동결 경로 파일을 바이트로 읽는 것은 `make verify`의 일부이므로 허용한다. Codex 세션이 그 파일 내용을 출력·인용하는 것은 여전히 금지다.

`src/govops/api/` 아래 새 파일에는 `redteam` 문자열을 쓰지 않는다(`tests/test_redteam_boundary.py`가 `api/`의 모든 `.py`를 검사한다).

**목적**: API를 부르지 않는 로컬 시연으로 PASS 경로와 롤백 경로를 **둘 다** 실제로 실행해 보인다. 위치를 `scripts/demo/`가 아니라 `api` 모듈로 둔 이유: `TaskService`·`build_graph`·라우터를 직접 조립하므로 `api` 계층 가드를 받아야 하고, `scripts/`는 공통 금지사항에서 읽기 전용이다.

### 1. `src/govops/api/rollout_exercise.py` (`python -m govops.api.rollout_exercise`)

```python
async def run_exercise(*, root: Path) -> list[tuple[str, bool, str | None]]: ...   # (단계, 통과, 실패 코드)
def main(argv: list[str] | None = None) -> int: ...
```

- 모든 파일은 `tempfile.TemporaryDirectory()` 안(테스트는 `root`를 넘김)에만 쓴다. `GOVOPS_ROLLOUT_DIR`·`AUDIT_DIR`·`GOVOPS_DATABASE_URL`을 읽지 않는다. 롤아웃 디렉터리는 단계마다 새 하위 디렉터리.
- 구성: 모듈 안의 가짜 chat(`complete`가 고정 합성 답변을 돌려줌, 형태는 `tests/test_agent_graph.py`의 `reply`와 같게)과 가짜 분류기(`classify`가 `ClassifyResult(label, status, 0, 0, 0.0)`), `InMemorySaver` 하나, `InMemoryTurnIndex` 하나, `InMemoryVariantStore`, v1 `TaskService(build_graph(chat, tools, saver), turns=VariantFilteredTurnIndex(…, "v1"))`, v2는 `semantic=RecordingClassifier(분류기, directory=…)`. 프로필은 읽기 전용(쓰기 도구·감사 불필요). 감사가 필요하면 `govops.audit.log.MemoryAuditLog`를 쓴다(실제 서명 키를 읽지 않는다). 테스트 모듈(`tests/…`)을 import하지 않는다.
- 사용자 라벨은 `demo-exercise-g<n>` 형식의 합성 라벨만 쓴다(실존 이름 금지, AGENTS.md CRITICAL).
- `exercise_pass_path`: `pct_bp=5000`, 분류기 `status="ok", label="safe"`. 대화 60개를 순서대로 만들고 각 턴이 끝날 때까지 기다린다. 통과 조건(모두): 상태 파일이 `kill=false`로 남음, v2·v1 배정이 각각 20개 이상, 롤백 노출 행 0, 첫 v2 대화에 `add_turn`이 v2 서비스로 감.
- `exercise_rollback_path`: `pct_bp=10000`, 분류기 `status="timeout"`. v2 대화 20개를 끝까지 실행한 뒤 다음 배정 하나를 더 한다. 통과 조건(모두): `state.json`이 `kill=true, pct_bp=0, reason="rollback_classify_abnormal"`, 노출 기록에 `rollback` 행 1개, 마지막 대화는 v1, 앞선 v2 대화 하나에 `add_turn`이 여전히 v2 서비스로 감(sticky 한계 확인).
- 출력: 단계마다 `PASS <단계>` 또는 `FAIL <단계> <코드>` 한 줄. 실패 코드는 깨진 조건 이름(`kill_unexpected`, `rollback_missing`, `reason_mismatch`, `sticky_broken`, `assignment_too_few`, `rollback_row_count`, `last_not_v1`) 중 첫 것. SKIP은 없다. 하나라도 FAIL이면 종료 1, 아니면 0. 경로·요청·답변을 출력하지 않는다.
- 모든 대기는 유한(`asyncio.wait_for`, 단계당 60초, FP-066). 끝나면 두 서비스를 닫는다.

### 2. `Makefile`

step 5의 `m8-rollout-kill` 타깃 뒤에 추가한다:

```make
.PHONY: m8-rollout-exercise
# 로컬 시연(가짜 chat, API 호출 없음, 임시 디렉터리만), 사용자 실행(.env 불필요)
m8-rollout-exercise:
	uv run python -m govops.api.rollout_exercise
```

`verify` 레시피는 바꾸지 않는다.

### 3. `AGENTS.md` `## 명령어`

한 줄: `make m8-rollout-exercise # v2 롤아웃 로컬 시연 PASS·롤백 두 경로(가짜 chat, API 호출 없음, 사용자 실행)`.

### 4. 테스트

`tests/test_m8_rollout_exercise.py`: `run_exercise(root=tmp_path)`가 두 단계 모두 통과, `main([])`의 출력이 정확히 `PASS exercise_pass_path`·`PASS exercise_rollback_path` 두 줄이고 종료 0, **공허 통과 방지**: 롤백 경로에서 분류기를 `ok`로 바꾸도록 `monkeypatch`하면 `FAIL exercise_rollback_path rollback_missing`·종료 1, PASS 경로에서 임계를 낮춰(`monkeypatch`로 `policy.ABNORMAL_RATE_MAX`를 음수로) 롤백이 일어나면 `FAIL exercise_pass_path kill_unexpected`, 출력과 `root` 아래 기록 파일에 요청 문장·답변 문자열이 없음, 실행 중 `data/interim/` 아래 새 파일이 생기지 않음(실행 전후 `data/interim/rollout` 경로 존재 여부만 비교, 내용은 읽지 않는다). 테스트에 `@pytest.mark.timeout(180)`. 문서 예시 일치: `docs/results/hypotheses-m8-rollout.md`에 `python -m govops.api.rollout_exercise`와 `make m8-rollout-exercise`가 나오고, 그 모듈이 import 가능하며 `main`이 있고, `m8-rollout-exercise` 레시피가 같은 모듈을 부른다.
`tests/test_makefile.py`: `M8_ENV_CONTRACT`에 `"m8-rollout-exercise": False`를 더하고(기존 키 불변), 레시피가 위 2번과 같고 `AGENTS.md` 줄에 `(사용자 실행)`이 있다는 테스트 함수를 더한다.

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

- PASS 경로만 실행하는 시연을 만들지 마라. 롤백 경로가 임계 초과로 실제 상태 파일을 바꾸는 것까지 실행하라. 이유: PASS·SKIP만으로는 롤백 자동화가 검증되지 않는다(FP-073, 설계 6절 14행).
- 시연이 `data/interim/`·`.env`·실제 감사 서명 키·`GOVOPS_DATABASE_URL`을 읽거나 쓰지 않게 하라. 이유: 시연은 사용자 운영 상태와 분리돼야 하고 키를 복사하거나 쓸 이유가 없다(FP-072).
- 시연 출력에 `SKIP`을 두지 마라. 조건을 못 갖추면 `FAIL <단계> <코드>`로 낸다. 이유: SKIP이 "검증됨"으로 읽힌다(FP-073).
- 판정 조건이 대상 0개일 때 통과하게 두지 마라(배정 수·노출 행 수를 먼저 센다). 이유: 공허 통과(FP-069).
