# Step 8: runner-anchors

## 읽어야 할 파일

- `/AGENTS.md`(계층 경계 표의 `evalrun/` 행: `audit`·`guardrails` import 금지), `/docs/ADR.md`(ADR-012, ADR-022), `/docs/THREAT_MODEL.md`(TH8)
- 이전 step 산출물: `/src/govops/api/tasks.py`(`TaskService.anchor`, `TaskService.audit`), `/src/govops/audit/log.py`(`AuditLog.directory`, `anchor`), `/src/govops/audit/anchor.py`(대기 파일 줄·`AuditAnchorError` 코드)
- `/src/govops/evalrun/dev_m3.py`(`run_dev_m3`, `_run_smoke`, 시도 디렉터리 `attempts/<task>-<n>/audit`, `service.aclose()` 지점), `/src/govops/evalrun/faults.py`(`execute_live`, `run_faults`, `service.aclose()` 지점), `/src/govops/evalrun/worktree.py`(청결 검사의 예외 경로)
- `/tests/test_evalrun_dev_m3.py`·`/tests/test_evalrun_faults.py`·`/tests/test_evalrun_driver.py`(서비스 팩토리 주입 방식: 파일 이름은 `rg -l service_factory tests`로 확인한다)
- `/.claude/skills/failure-patterns/SKILL.md`(FP-031·054·064) — 읽기만 한다

## 작업

**수정할 파일**(이 목록이 전부다): `src/govops/evalrun/anchors.py`(신규), `src/govops/evalrun/dev_m3.py`, `src/govops/evalrun/faults.py`, `tests/test_evalrun_anchors.py`(신규), `tests/test_evalrun_dev_m3.py`·`tests/test_evalrun_faults.py`(가짜 서비스에 `anchor`가 필요해진 것에 맞춘 갱신에 한함, 단언 약화 금지). 계층: `evalrun/`은 `audit`·`guardrails`·`broker`·`mocks`·LLM SDK를 import할 수 없다. 고정점은 `service.anchor(label)`(api 계층의 `TaskService`)로만 만든다. TDD.

열람 금지 경로: `data/interim/`
수정 허용 경로: `src/govops/evalrun/anchors.py`, `src/govops/evalrun/dev_m3.py`, `src/govops/evalrun/faults.py`, `tests/test_evalrun_anchors.py`, `tests/test_evalrun_dev_m3.py`, `tests/test_evalrun_faults.py`

**배경(ADR-012·TH8 "실행마다 고정점")**: dev·faults 러너는 실행이 끝날 때 사용한 감사 디렉터리마다 고정점을 만든다. 고정점 줄은 대기 파일(`data/interim/audit/anchors-pending.jsonl`)에만 쓰이고(step 4), 결과 문서 PR이 `docs/results/audit-anchors.jsonl`로 옮긴다. 러너는 `docs/results/`를 쓰지 않는다(러너의 git 청결 검사: `worktree.py`는 예외 경로 1개만 허용한다).

### 1. `evalrun/anchors.py`

- `anchor_label(prefix: str, run_name: str) -> str`: `f"{prefix}-{run_name}"`을 소문자로 바꾸고 `[a-z0-9-]` 밖 문자를 `-`로 바꾸고 앞뒤 `-`를 떼고 40자로 자른다. 결과가 `^[a-z0-9][a-z0-9-]{0,39}$`를 만족하지 못하면(빈 값 등) `ValueError`. 러너는 `dev`·`faults` 접두어와 실행 디렉터리 이름(uuid hex)을 쓴다.
- `AnchorTracker`: `track(service)`(서비스를 열 때마다 호출. 감사 디렉터리 `service.audit.directory`별로 **마지막으로 등록된 서비스**를 기억한다. **어떤 예외도 올리지 않는다**: `audit`이 `None`이거나 `directory` 속성이 없는 sink(예: `MemoryAuditLog`·가짜 서비스)는 등록하지 않는다), `finish(label) -> list[str]`(기억한 디렉터리마다 `service.anchor(label)`을 한 번씩 호출한다). 반환값은 출력 줄 목록이다. **어떤 예외도 올리지 않는다**(fail-open): 실패한 디렉터리는 `audit_anchor_failed:<예외 클래스 이름>` 줄(메시지·경로 없음)로 돌려주고 다른 디렉터리는 계속한다. 성공 줄은 `audit_anchor <canonical JSON 한 줄>`(고정점 dict: 해시·서명·kid·상대 경로 `dir`·라벨·시각만 있고 비밀이 없다). `service.anchor`가 없는 가짜 서비스·`RuntimeError("audit_anchor_unsupported")`는 건너뛰고 줄을 만들지 않는다.
- 러너가 줄을 stdout에 그대로 출력한다. 러너의 결과(성공·실패 판정, 매니페스트, 종료 코드)는 고정점 성공·실패에 영향받지 않는다(이미 비용을 쓴 live 실행의 결과를 고정점 문제로 버리지 않는다. 고정점이 없으면 `audit-verify`가 꼬리 잘라내기를 탐지하지 못할 뿐이다).

### 2. 러너 연결

- `dev_m3.py`: 서비스를 만드는 모든 지점(`_run_smoke`의 단계별 서비스, `run_dev_m3`의 시도별 서비스)에서 서비스를 만든 직후 `tracker.track(service)`를 부르고, **실행이 끝날 때 한 번** `tracker.finish(anchor_label("dev", <실행 디렉터리 이름>))`을 호출한다(시도별 디렉터리 `attempts/<task>-<n>/audit`와 smoke의 실제 감사 디렉터리가 각각 한 번씩 고정점을 받는다). 이미 `await service.aclose()`가 있는 `finally` 흐름·예외 처리·결과 판정 로직은 바꾸지 않는다. `finish`는 서비스를 닫은 뒤에도 감사 객체를 쓸 수 있으므로 `aclose` 뒤에 부른다.
- `faults.py`: `execute_live`의 행별 서비스에 `track`을, 실행(`run_faults`/`execute_live`)이 끝날 때 한 번 `finish(anchor_label("faults", <실행 디렉터리 이름>))`을 호출한다. 행마다 새 서비스를 만들어도 같은 감사 디렉터리의 마지막 서비스 하나로 한 번만 고정점이 만들어진다.
- 서비스 팩토리 주입(`service_factory`) 경로의 가짜 서비스가 `audit`·`anchor`를 가지지 않아도 러너가 깨지지 않는다(위 건너뜀 규칙).

### 3. 테스트 (`tests/test_evalrun_anchors.py`)

- `anchor_label`: 대문자·밑줄·긴 이름 정규화, 빈 이름 거부, 항상 라벨 정규식 통과(시드 고정 무작위 1,000개 `uuid4().hex`).
- `AnchorTracker`(가짜 서비스: `audit.directory`와 `anchor` 호출 기록): 같은 디렉터리 서비스 3개를 `track`하면 `finish`가 **마지막 서비스로 한 번만** `anchor`를 부르고, 다른 디렉터리 2개면 각각 한 번이다. 성공 줄 형식, 실패하는 `anchor`(임의 예외) → `audit_anchor_failed:<이름>` 줄이고 예외가 올라오지 않고 다른 디렉터리 처리는 계속된다. `anchor`가 없는 가짜·`audit_anchor_unsupported`는 줄 없이 건너뜀. `track` 케이스: `audit=None`인 가짜 서비스, `MemoryAuditLog`를 가진 가짜 서비스, `audit`에 `directory`가 없는 객체는 예외 없이 등록되지 않고 `finish`가 그 서비스에 `anchor`를 부르지 않는다. 실패 줄에 예외 메시지·경로가 없다.
- 러너 연결: 가짜 서비스 팩토리로 `run_dev_m3`(smoke 경로와 시도 경로)와 faults의 `execute_live`를 돌려 `finish`가 실행 끝에 한 번 호출되고 라벨이 `dev-…`·`faults-…`이며 러너의 결과·종료 판정이 고정점 실패 주입에서도 같다. 기존 러너 테스트의 의미 있는 단언은 그대로 통과한다.
- 계층: `evalrun/`이 `govops.audit`을 import하지 않는다는 기존 가드가 통과한다.

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

## 공통 금지사항

<!-- common-block:start -->
- **선점검**: 코드를 쓰기 전에 이 파일의 작업 지시·금지사항·"수정할 파일" 목록을 서로 대조한다. 충돌(하나가 요구한 것을 다른 것이 금지함, 수정할 파일 목록 밖 파일의 수정이 필요함, 계층 경계 위반)이 있으면 코드를 쓰지 않고 즉시 `blocked`로 보고한다. reason에는 충돌하는 두 문장을 그대로 인용한다. 충돌이 없으면 진행한다.
- **실제 서비스**: 실제 서비스(A MCP·LiteLLM·mock 컨테이너·외부 API)에 접속하는 코드는 작성할 수 있다. step 안에서 그 코드를 실행하거나 그 코드에 접속하는 테스트를 만들지 않는다. 테스트는 가짜 환경(가짜 실행기·로컬 루프백·합성 데이터)으로만 한다.
- **계층 경계**: `AGENTS.md`의 계층 경계 표와 `tests/test_architecture.py`를 따른다. 가드 테스트를 약화하지 않는다.
- **읽기 전용**: `.claude/`, `docs/results/`의 사전 등록 파일, `scripts/`, `phases/`, `data/contract/`, `data/eval/`, `data/replay/`·`data/results/`의 기록, A 저장소(`~/PycharmProjects/ko-docs-agentic-rag`)는 읽기만 한다(이 step의 "수정할 파일"에 있는 것만 예외). 기존 테스트의 단언을 약화하지 않는다.
- **비밀 값**: `.env`와 A의 `data/interim/mcp/tokens.json`을 열거나 출력하지 않는다. 토큰·키·Authorization 헤더를 코드·테스트·문서·로그·trace에 남기지 않는다.
<!-- common-block:end -->


## 이 step의 금지사항


- `evalrun/`에서 `govops.audit`을 import하지 마라. 이유: 계층 경계 표와 `tests/test_architecture.py`가 막는다. 고정점은 `service.anchor(label)`로만 호출한다.
- 고정점 실패로 러너의 결과·종료 판정·매니페스트를 바꾸지 마라. 이유: 비용을 쓴 live 실행을 부수적인 고정점 오류로 무효화하면 안 된다. 실패는 `audit_anchor_failed:<이름>` 줄로만 알린다(예외 메시지·경로 금지).
- `docs/results/audit-anchors.jsonl`이나 저장소의 다른 추적 파일을 러너가 쓰게 하지 마라. 이유: 러너의 git 청결 검사(`worktree.py`는 예외 경로가 비용 원장 하나뿐)가 실패하고 live 실행이 시작 전에 막힌다(FP-031·064).
- 기존 `finally`·`aclose`·예외 처리 구조를 바꾸지 마라. 이유: 서비스·도구 정리와 결과 판정이 이 구조에 의존한다. 고정점 호출만 덧붙인다(FP-054: 수정 범위 밖 변경 금지).
- 행·시도마다 고정점을 만들지 마라. 이유: 체인은 누적이라 실행 끝의 한 번이 이전 기록을 모두 덮는다. 매번 만들면 `chain.anchor`와 대기 파일 줄이 불필요하게 쌓인다.
