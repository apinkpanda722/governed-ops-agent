# Step 5: service-start-event

## 읽어야 할 파일

- `/AGENTS.md`(계층 경계 표의 `api/` 행, 아키텍처 규칙의 감사 로그), `/docs/ADR.md`(ADR-012), `/docs/ARCHITECTURE.md`
- 이전 step 산출물: `/src/govops/audit/log.py`(`AuditLog.anchor`, `service.start` 필드 규칙), `/src/govops/audit/crypto.py`(`AuditKeyError`), `/tests/conftest.py`(autouse 환경 격리)
- `/src/govops/api/wiring.py`(`build_task_service`, `open_task_service`, `StartupError`), `/src/govops/api/tasks.py`(`TaskService`, `aclose`), `/src/govops/guardrails/policy.py`(`team_policy_sha256`), `/src/govops/gateway/hook_digest.py`(`compute_digests`, `rules_sha256_full`), `/data/policy/team_policy.json`·`/data/seed/access_index.json`(해시 대상: 이름만 안다)
- `/tests/test_api_wiring_build.py`·`/tests/test_api_wiring.py`(기존 조립 테스트)
- `/.claude/skills/failure-patterns/SKILL.md`(FP-060·061) — 읽기만 한다

## 작업

**수정할 파일**(이 목록이 전부다): `src/govops/api/wiring.py`, `src/govops/api/tasks.py`, `tests/test_service_start_event.py`(신규), `tests/test_api_wiring_build.py`·`tests/test_api_wiring.py`·`tests/test_evalrun_dev_m3.py`·`tests/test_evalrun_faults.py`·`tests/test_obs_instrument.py`·`tests/test_api_smoke.py`·`tests/test_e2e_isolation.py`·`tests/test_golden_m3.py`(`build_task_service`·`TaskService`를 쓰는 소비자: `service.start` 기록·키 요구·감사 디렉터리 내용에 맞춘 갱신에 한함, 단언 약화 금지). 계층: `api/`는 `broker`·`mocks`·LLM SDK를 import할 수 없다. TDD.

열람 금지 경로: `data/interim/`
수정 허용 경로: `src/govops/api/wiring.py`, `src/govops/api/tasks.py`, `tests/test_service_start_event.py`, `tests/test_api_wiring_build.py`, `tests/test_api_wiring.py`, `tests/test_evalrun_dev_m3.py`, `tests/test_evalrun_faults.py`, `tests/test_obs_instrument.py`, `tests/test_api_smoke.py`, `tests/test_e2e_isolation.py`, `tests/test_golden_m3.py`

열람 금지의 범위: 이 금지는 **Codex 세션이 `data/interim/`의 파일을 직접 열람·출력·인용하는 것**에 대한 것이다. 이 step이 작성하는 코드나 테스트가 실행 시 `data/interim/` 아래 경로를 프로그램으로 읽거나 쓰는 것(기본 경로 문자열, 기본 `AUDIT_DIR`, 대기 파일 경로 포함)은 금지 대상이 아니다. 단 테스트는 `data/interim/`의 실제 파일 **내용**을 읽지 않고(저장소 보호 스냅샷 픽스처는 파일 이름·크기·`mtime_ns`만 읽는다), 쓰기는 임시 디렉터리에서만 한다.

**배경**: `build_task_service`는 서비스, dev 러너, faults 러너가 모두 쓰는 조립 지점이다. 여기서 `service.start`를 기록하면 세 경로가 모두 키 없이는 시작하지 않고 시작 시 정책·규칙·접근 인덱스·훅 해시가 체인에 남는다. 러너가 행·시도마다 서비스를 만들면 `service.start`가 그만큼 쌓이는 것은 정상이다(genesis는 디렉터리당 한 번).

### 1. `service.start` 기록 (`wiring.py`)

- `build_task_service`가 `AuditLog(...)`를 만든 직후, 서비스를 돌려주기 전에 `audit.append("service.start", task_id=None, actor_sub="system", policy_sha256=…, rules_sha256=…, access_index_sha256=…, hook_rules_digest=…, hook_code_digest=…)`를 한다.
  - `policy_sha256` = `govops.guardrails.policy.team_policy_sha256(경로)`(이미 있는 함수, `data/policy/team_policy.json`의 바이트 SHA-256 hex. 새로 구현하지 않는다), `access_index_sha256` = `data/seed/access_index.json`의 SHA-256 hex, `rules_sha256` = `govops.gateway.hook_digest.rules_sha256_full(root)`, `hook_rules_digest`·`hook_code_digest` = `compute_digests(root)`의 `rules`·`code`. `root`는 `open_task_service`가 쓰는 것과 같은 `Path(__file__).resolve().parents[3]`다.
- 실패 처리(시작 거부): `AuditKeyError`는 `StartupError(error.code)`로, `AuditWriteError`는 `StartupError("audit_write_failed")`로 바꿔 올린다(서비스·러너 시작 거부, 부분 조립 상태를 남기지 않는다). 해시 대상 파일을 읽을 수 없는 오류는 `StartupError("audit_start_hash_unavailable")`. 예외 메시지에 경로·키 내용을 넣지 않는다. `StartupError`가 이미 있는 것과 맞게 쓴다.
- `build_task_service`의 기존 시그니처·주입 동작(`audit_dir` 인자, 환경 변수를 읽지 않는 조립)은 유지한다. `open_task_service`는 `AUDIT_DIR`·키 환경 변수를 이전과 같은 방식(`env_or`)으로만 쓴다.

### 2. `TaskService.anchor` (`tasks.py`)

- `TaskService.anchor(label: str, *, repo_root: Path | None = None) -> dict`: `self.audit.anchor(label, repo_root=repo_root)`로 `repo_root`를 **그대로 넘겨**(생략하면 `None`) 돌려준다(러너가 임시 저장소 루트 같은 자기 루트를 넘길 수 있어야 하고, 기본값(실제 저장소 루트)에 기대면 임시 디렉터리를 쓰는 테스트가 `audit_anchor_dir_outside_repo`로 실패한다. 러너가 `audit`을 import하지 않고 고정점을 만들 수 있게 한다). `audit`이 없거나 `anchor`를 지원하지 않으면(`MemoryAuditLog`) `AttributeError`가 아니라 `RuntimeError("audit_anchor_unsupported")`.

### 3. 테스트 (`tests/test_service_start_event.py`)

- 가짜 채팅·도구 주입(`tests/test_api_wiring_build.py`의 방식)으로 `build_task_service(..., audit_dir=tmp)`를 호출하면 체인의 두 번째 레코드(첫째는 genesis)가 `service.start`이고 `actor.sub == "system"`·`task_id is None`이며 다섯 필드가 형식(64 hex 셋·12 hex 둘)을 만족하고 실제 파일 해시와 같다(테스트가 같은 파일을 직접 해시해 대조).
- 같은 디렉터리에 서비스를 두 번 만들면 `service.start`가 두 건이고 genesis는 하나다(체인이 `verify_chain`으로 `ok`).
- 시작 거부: `AUDIT_KEY_FILE`을 없는 경로로 바꾸면 `StartupError("audit_key_missing")`이고 서비스가 만들어지지 않으며 체인 파일이 생기지 않는다. 권한이 `0644`인 키 → `audit_key_permissions`. 체인 디렉터리를 쓸 수 없게 만들면(부모가 파일) `audit_write_failed`.
- `TaskService.anchor`: 실제 `AuditLog`이면 고정점 dict를 돌려주고(`repo_root` 밖이라 `AuditAnchorError(audit_anchor_dir_outside_repo)`가 올라오는 것은 허용: 임시 디렉터리가 저장소 밖이므로 이 테스트는 `AuditLog.anchor`를 가짜로 바꿔 위임만 검증), `MemoryAuditLog`면 `RuntimeError("audit_anchor_unsupported")`.
- `TaskService.anchor` 인자 전달: `audit.anchor`를 받은 인자를 기록하는 가짜로 바꾼 서비스에서 `anchor("x", repo_root=<임시 루트>)`는 가짜가 `label="x"`, `repo_root=<임시 루트>`를 받게 하고, `repo_root`를 생략하면 `repo_root=None`을 받게 한다. 실제 `AuditLog`(체인 디렉터리가 임시 루트 아래)에 `repo_root=<임시 루트>`를 넘기면 고정점 dict를 돌려주고 `dir`이 임시 루트 기준 상대 경로다.
- `tests/test_api_wiring_build.py`·`tests/test_api_wiring.py`: `AUDIT_DIR`·감사 레코드를 읽는 기존 단언을 새 형식(체인 파일·`service.start` 존재)에 맞게 갱신하되 의미를 약화하지 않는다.

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


- 해시를 다른 `govops` 계층에서 새로 계산하는 코드를 만들지 마라. 이유: 훅 해시는 `gateway.hook_digest`가 단일 출처다. 중복 구현은 훅 정합 검사와 값이 어긋난다.
- 키 오류·쓰기 오류 메시지에 키 경로·파일 내용·예외 원문을 넣지 마라. 이유: 비밀 값 규칙이다. 고정 코드만 올린다.
- `service.start` 기록이 실패했는데 서비스를 계속 조립하지 마라. 이유: 감사 로그 없이 시작한 서비스는 이후 부작용 감사도 보장하지 못한다. 시작을 거부한다.
- `tests/test_api_wiring_build.py`·`tests/test_api_wiring.py`의 갱신에서 감사 관련 단언을 삭제하거나 느슨하게 하지 마라. 이유: 기록 위치·형식이 바뀐 것이지 기록 의무가 바뀐 것이 아니다(FP-060). 레코드 개수를 정확히 단언하는 곳이 있으면 시스템 이벤트를 제외한 이름 기준으로 바꾼다(FP-061).
- 러너·서비스에서 `make audit-anchor`·`make audit-verify`·`make smoke-audit`을 실행하지 마라. 이유: 사용자 체인과 키를 쓴다. 이 step의 테스트는 임시 디렉터리와 임시 키로만 돈다.
