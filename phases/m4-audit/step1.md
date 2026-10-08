# Step 1: audit-events

## 읽어야 할 파일

- `/AGENTS.md`(계층 경계 표의 `audit/` 행, 아키텍처 규칙의 감사 로그), `/docs/ADR.md`(ADR-012, ADR-018 6항, ADR-023), `/docs/ARCHITECTURE.md`(감사 로그 절)
- 이전 step 산출물: `/src/govops/audit/crypto.py`(`format_usd`, `canonical_json`), `/tests/audit_keys.py`, `/tests/conftest.py`(autouse 환경 격리)
- `/src/govops/audit/log.py`·`/src/govops/audit/__init__.py`(현재 이벤트·필드 검증, `MemoryAuditLog`), `/tests/test_audit.py`
- `/src/govops/gateway/hook_header.py`(`_PRE`·`_POST` 값 집합: 읽기만 한다. `audit/`가 이 모듈을 import하지는 않는다), `/src/govops/gateway/client.py`(`ChatResult` 필드 `deployment_id`·`model_group`), `/infra/litellm/config.yaml`(`model_name` 별칭)
- `/.claude/skills/failure-patterns/SKILL.md`(FP-010·011·061) — 읽기만 한다

## 작업

**수정할 파일**(이 목록이 전부다): `src/govops/audit/log.py`, `src/govops/audit/__init__.py`, `tests/test_audit.py`(이벤트 집합·시스템 이벤트 단언 갱신에 한함, 기존 단언 약화 금지), `tests/test_audit_events.py`(신규). 이 step은 **스키마 검증만** 바꾼다. 파일 쓰기 방식(비체인 JSONL)은 다음 step이 바꾼다. 계층: `audit/`는 `agent`·`tools`·`broker`·`api`를 import할 수 없고 `gateway`도 import하지 않는다(값 집합은 복제하고 일치를 테스트가 확인한다). TDD.

열람 금지 경로: `data/interim/`
수정 허용 경로: `src/govops/audit/log.py`, `src/govops/audit/__init__.py`, `tests/test_audit.py`, `tests/test_audit_events.py`

열람 금지의 범위: 이 금지는 **Codex 세션이 `data/interim/`의 파일을 직접 열람·출력·인용하는 것**에 대한 것이다. 이 step이 작성하는 코드나 테스트가 실행 시 `data/interim/` 아래 경로를 프로그램으로 읽거나 쓰는 것(기본 경로 문자열, 기본 `AUDIT_DIR`, 대기 파일 경로 포함)은 금지 대상이 아니다. 단 테스트는 `data/interim/`의 실제 파일 **내용**을 읽지 않고(저장소 보호 스냅샷 픽스처는 파일 이름·크기·`mtime_ns`만 읽는다), 쓰기는 임시 디렉터리에서만 한다.

**배경**: 다음 step의 체인이 첫 쓰기에서 `chain.genesis`(시스템 이벤트)를 쓰므로 이벤트 스키마가 체인보다 먼저 있어야 한다. `Field` 타입은 `str | int | bool | None`을 유지한다(float 금지). 일반 경로(해시·mock ID·허용 enum 외 문자열은 거부, 형식 밖 문자열에는 PII 검사)는 그대로 둔다.

### 1. 이벤트와 행위자

- `EVENTS`에 5개를 추가한다: `service.start`, `model.call`, `auth.failed`, `chain.genesis`, `chain.anchor`.
- `SYSTEM_EVENTS = frozenset({"service.start", "chain.genesis", "chain.anchor", "auth.failed"})`. 이 이벤트만 `actor_sub == "system"`이고 `task_id is None`이어야 한다(둘 다 위반 시 거부). 나머지 이벤트는 기존처럼 `task_id`(`[0-9a-f]{32}`)·`actor_sub`(`demo-[a-z-]+-g[0-9]+`)가 필수이고 `"system"`·`None`은 거부한다. `AuditPort.append`의 `task_id` 타입은 `str | None`이다. `auth.failed`는 인증 전이라 task가 없고 행위자를 식별할 수 없어 시스템 이벤트에 둔다.
- `MemoryAuditLog.append`도 **같은 검증**을 하고(시스템 이벤트 규칙 포함), 저장 레코드는 기존 형태(`event`, `task_id`, `actor_sub`, 필드)를 유지한다. 서명·체인 필드는 만들지 않는다(`MemoryAuditLog`는 서명하지 않는다).

### 2. 이벤트별 필수·허용 필드 (신규 이벤트에만 적용, 기존 이벤트의 필드 규칙은 바꾸지 않는다)

| 이벤트 | 필수 필드 | 형식 |
|---|---|---|
| `service.start` | `policy_sha256`, `rules_sha256`, `access_index_sha256`, `hook_rules_digest`, `hook_code_digest` | 앞 셋은 `^[0-9a-f]{64}$`, 뒤 둘은 `^[0-9a-f]{12}$` |
| `chain.genesis` | `legacy_files`, `legacy_lines`, `legacy_last_seq`, `legacy_sha256` | 앞 셋은 0 이상의 `int`(bool 거부), `legacy_sha256`은 `^[0-9a-f]{64}$` |
| `chain.anchor` | `label` | `^[a-z0-9][a-z0-9-]{0,39}$` |
| `model.call` | `team`, `model`, `deployment_id`, `fallback`, `cost_usd` + 선택 묶음 `guardrail_pre`·`guardrail_post`·`guardrail_q`·`guardrail_m`(네 개가 모두 있거나 모두 없어야 한다) | `team`은 기존 팀 형식 또는 `none`, `model`은 `^(?:agent-[a-z-]{1,30}\|unknown)$`, `deployment_id`는 `^(?:[a-z0-9][a-z0-9.-]{0,63}\|unknown)$`, `fallback`은 `bool`, `cost_usd`는 `^\d+\.\d{6}$`, `guardrail_pre`는 `{allow, block, unavailable, team_unknown, probe}`, `guardrail_post`는 `{ok, flag, error, none}`, `guardrail_q`·`guardrail_m`은 `0..10000`의 `int`(bool 거부) |
| `auth.failed` | `reason` + 선택 `suppressed`(`int` ≥ 1) | `reason`은 `{missing_credentials, invalid_token, unknown_user, loopback_required}` (기존 `REASONS` 집합에 합친다) |

- **새 필드 이름**(`policy_sha256`·`rules_sha256`·`access_index_sha256`·`hook_rules_digest`·`hook_code_digest`·`legacy_files`·`legacy_lines`·`legacy_last_seq`·`legacy_sha256`·`label`·`model`·`deployment_id`·`fallback`·`cost_usd`·`guardrail_pre`·`guardrail_post`·`guardrail_q`·`guardrail_m`·`suppressed`)은 **표의 이벤트에서만** 허용한다(다른 이벤트에 쓰면 거부). **기존 필드** `team`(`guardrail.*` 이벤트)·`reason`(`side_effect.*` 이벤트)은 기존 이벤트에서 기존 규칙 그대로 둔다(제한을 걸지 않는다). 이름이 같아도 `model.call`의 `team`은 기존 팀 형식 또는 `none`만, `auth.failed`의 `reason`은 위 4개 enum만 허용하고, `side_effect.*`의 `reason`에 auth 사유 4개는 쓸 수 없다. 기존 `guardrail`·`side_effect` 이벤트 테스트는 그대로 통과해야 한다. 필수 필드 누락·미정의 필드는 거부한다. 원문 필드(`text`·`body` 등)는 기존 금지 목록 그대로이고 `kid`를 금지 필드 이름에 추가한다(체인 필드는 로그가 직접 채운다).
- 형식이 정해진 값(64 hex·12 hex·`label`·`cost_usd`·enum)은 **형식으로만 검증하고 PII 정규식을 돌리지 않는다**(FP-010). 형식 밖 값은 PII 검사 없이 거부한다.
- `AuditLog.append`(현재 비체인 JSONL)도 같은 검증과 시스템 이벤트 규칙을 쓴다. `task_id`가 `None`이면 JSON `null`로 쓴다.

### 3. 테스트

- `tests/test_audit.py`: `assert len(EVENTS) == 14`를 **이름 집합** 단언으로 바꾼다(기존 14개 이름 + 신규 5개 이름을 모두 적은 집합). 이 이벤트 순회 테스트가 신규 이벤트에도 맞게 갱신되었는지(신규 이벤트는 필수 필드가 있어야 하므로 별도 케이스) 확인한다. 기존 단언은 약화하지 않는다.
- `tests/test_audit_events.py`: 시스템 이벤트는 `system`·`None`만 통과하고 일반 이벤트에 `None`·`"system"`은 거부, 이벤트별 필수 필드 누락·형식 위반·다른 이벤트에서의 사용 거부, 선택 묶음 부분 지정 거부, `bool`을 `int`로 쓴 값 거부, `float` 값 거부. `MemoryAuditLog`가 `AuditLog`와 같은 입력에서 같은 판정을 한다(허용·거부 표를 두 구현에 같이 돌린다). **`guardrail_pre`·`guardrail_post` 집합이 `govops.gateway.hook_header._PRE`·`_POST`와 같다**는 테스트(`audit/`는 import하지 않고 테스트만 import). `model` 정규식이 `infra/litellm/config.yaml`의 `model_name` 별칭 4개를 모두 통과.
- **무작위 대량 테스트(FP-010·011, 시드 고정 `random.Random(20261008)`, 건수 명시)**: 64 hex 무작위 10,000개가 `policy_sha256`·`rules_sha256`·`access_index_sha256`·`legacy_sha256` 필드에서 모두 통과(카드번호·전화번호 규칙에 걸리는 값이 섞여 있어도), 12 hex 무작위 10,000개가 `hook_rules_digest`·`hook_code_digest`에서 모두 통과, `uuid4().hex` 무작위 10,000개가 `task_id`로 통과. 형식 밖 값(63자·대문자·전화번호 모양 숫자열)은 거부.
- `uuid`·난수·시간을 `monkeypatch`로 고정해 실패를 덮지 않는다.

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


- 해시·`label`·`cost_usd`·식별자 필드에 PII 정규식을 돌리지 마라. 이유: 무작위 sha256 hex의 약 1%가 카드번호 규칙에, `uuid4().hex`의 약 0.45%가 전화번호 규칙에 걸려 정상 기록이 무작위로 실패했다. 형식 검증이 먼저고 형식 밖 값은 거부한다(FP-010).
- 무작위로 실패하는 테스트를 입력 고정(`monkeypatch`·고정 ID)으로 덮지 마라. 이유: 실제 결함을 가린다. 시드 고정 대량 테스트로 검증한다(FP-011).
- 이벤트 개수(`len(EVENTS) == N`)를 단언하지 마라. 이유: 뒤 step이 이벤트를 더하면 깨진다. 이름 집합으로 단언한다(FP-061).
- `audit/log.py`에서 `govops.gateway`를 import하지 마라. 이유: 값 집합은 복제하고 일치는 테스트가 확인한다. 계층을 단순하게 둔다.
- 파일 쓰기 방식·파일 이름·체인 필드(`prev_hash`·`hash`·`sig`)를 이 step에서 바꾸지 마라. 이유: 다음 step의 범위다. 이 step의 변경은 스키마 검증뿐이다.
