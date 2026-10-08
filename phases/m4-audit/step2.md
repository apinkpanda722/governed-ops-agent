# Step 2: audit-log-chain

## 읽어야 할 파일

- `/AGENTS.md`(감사 로그 규칙), `/docs/ADR.md`(ADR-012, ADR-018 6항), `/docs/ARCHITECTURE.md`(감사 로그 절)
- 이전 step 산출물: `/src/govops/audit/crypto.py`(step 0: `load_signing_key`, `SigningKey.sign`, `record_hash`, `canonical_json`, `AuditKeyError`), `/src/govops/audit/log.py`(step 1: `SYSTEM_EVENTS`, 이벤트별 검증, `MemoryAuditLog`), `/tests/audit_keys.py`, `/tests/conftest.py`, `/tests/test_audit.py`, `/tests/test_audit_events.py`
- `/src/govops/tools/effects.py`·`/src/govops/agent/graph.py`·`/src/govops/api/wiring.py`(감사 사용처: 시그니처는 유지한다)
- `/.claude/skills/failure-patterns/SKILL.md`(FP-004·012·032·066) — 읽기만 한다
- **외부 계약 실측**(macOS `fcntl.flock`, 일회용 프로브. **성공 여부와 반환 내용을 따로 적는다**):

| 지점 | 성공 여부 | 반환 내용 |
|---|---|---|
| 디렉터리 fd(`os.open(dir, O_RDONLY)`)에 `flock(LOCK_EX)` | 동작 | 디렉터리에 파일이 생기지 않는다(`os.listdir` 빈 목록). 같은 잠금을 다른 프로세스에서 `LOCK_NB`로 시도하면 보유 중 `BlockingIOError`, 해제 후 성공 |
| 같은 프로세스의 두 번째 fd | 동작 | 이미 잠겨 있으면 `BlockingIOError`(프로세스 안에서도 인스턴스끼리 배제된다) |
| CI(Linux)에서의 같은 동작 | **미실측** | 경합 테스트가 CI에서 처음 확인한다. 결과는 프로세스 간 불변식으로 단언한다 |

## 작업

**수정할 파일**(이 목록이 전부다): `src/govops/audit/log.py`, `src/govops/audit/__init__.py`, `tests/test_audit_chain.py`(신규), `tests/test_audit.py`·`tests/test_audit_events.py`(체인 파일 이름·레코드 필드 단언과, 첫 `append`가 genesis(`seq`=1)를 먼저 쓰므로 반환 seq가 2가 되는 것에 맞춘 갱신에 한함, 기존 단언 약화 금지: 같은 이벤트·필드 값을 같은 강도로 단언한다), `tests/test_api_tasks.py`·`tests/test_agent_graph.py`·`tests/test_effects_matrix.py`·`tests/test_agent_scope.py`·`tests/test_evalrun_driver.py`(감사 디렉터리의 파일 이름·레코드 형식을 읽는 단언의 갱신에 한함, 단언 약화 금지. `tests/test_api_tasks.py`에 한해 동시 승인 응답의 **순서 단언**을 순서 무관 불변식으로 바꾸는 갱신도 허용한다: 아래 테스트 절 참조). 계층: `audit/`는 `agent`·`tools`·`broker`·`api`를 import할 수 없다. TDD.

열람 금지 경로: `data/interim/`
수정 허용 경로: `src/govops/audit/log.py`, `src/govops/audit/__init__.py`, `tests/test_audit_chain.py`, `tests/test_audit.py`, `tests/test_audit_events.py`, `tests/test_api_tasks.py`, `tests/test_agent_graph.py`, `tests/test_effects_matrix.py`, `tests/test_agent_scope.py`, `tests/test_evalrun_driver.py`

열람 금지의 범위: 이 금지는 **Codex 세션이 `data/interim/`의 파일을 직접 열람·출력·인용하는 것**에 대한 것이다. 이 step이 작성하는 코드나 테스트가 실행 시 `data/interim/` 아래 경로를 프로그램으로 읽거나 쓰는 것(기본 경로 문자열, 기본 `AUDIT_DIR`, 대기 파일 경로 포함)은 금지 대상이 아니다. 단 테스트는 `data/interim/`의 실제 파일 **내용**을 읽지 않고(저장소 보호 스냅샷 픽스처는 파일 이름·크기·`mtime_ns`만 읽는다), 쓰기는 임시 디렉터리에서만 한다.

**배경**: `AuditLog`를 해시체인 + 서명으로 바꾼다. 쓰는 곳은 이 클래스 하나다. `append` 시그니처(`append(event, *, task_id, actor_sub, **fields) -> int`)와 `append_with_retry`·`MemoryAuditLog`는 유지한다. `MemoryAuditLog`는 서명하지 않는다.

### 1. 저장 형식

- 체인 파일: `<AUDIT_DIR>/chain-YYYY-MM-DD.jsonl`(날짜는 **UTC** `ts` 기준). 기존 `YYYY-MM-DD.jsonl`(legacy)은 **읽기만 하고 수정·삭제하지 않는다**. 체인 로직은 `chain-*.jsonl`만 읽는다.
- 레코드: `{"seq", "ts", "prev_hash", "actor": {"sub", "act": "governed-ops-agent"}, "event", "task_id", <필드…>, "kid", "hash", "sig"}`. `ts`는 UTC `isoformat()`(naive 시각은 `AuditWriteError`). `hash` = `crypto.record_hash(hash·sig를 뺀 레코드)`(`kid`·`prev_hash` 포함). `sig` = `SigningKey.sign(hash)`. 한 줄은 `canonical_json(레코드) + "\n"`이다. 파일은 `0600`, 디렉터리는 `0700`.
- `AuditLog.__init__(directory=None, *, clock=_utc_now, lock_timeout: float = 10.0)`: 디렉터리는 기존처럼 `env_or("AUDIT_DIR", "data/interim/audit")`. 생성자는 `crypto.load_signing_key()`를 호출한다. **키 문제(`AuditKeyError`)는 변환하지 않고 그대로 올린다**(서비스·러너 시작 거부). 생성자는 디렉터리를 만들고 권한을 맞추는 일만 하고 잠금·genesis는 첫 `append`에서 한다. 인스턴스 속성 `directory`는 유지한다.
- `AuditWriteError`에 `code` 속성을 추가한다(`AuditWriteError(message, code="audit_write_failed")`, 하위 클래스 `_InvalidAuditField`는 기존 동작 유지). 새 코드: `audit_lock_timeout`, `audit_chain_corrupt`, `audit_legacy_invalid`.

### 2. 잠금

- 쓰기마다 **디렉터리 fd에 `fcntl.flock(LOCK_EX | LOCK_NB)`** 를 재시도하며 건다. 재시도 간격은 5ms에서 시작해 50ms 상한까지 늘린다. 마감은 `lock_timeout`(기본 10.0초, `time.monotonic` 기준)이고 넘으면 `AuditWriteError(code="audit_lock_timeout")`(fail-closed, 아무것도 쓰지 않는다). 잠금 파일을 만들지 않는다(결과 디렉터리에 커밋될 수 있는 부산물을 남기지 않기 위해서다). 프로세스 안 스레드 배제용 `threading.Lock`을 정규화한 디렉터리별로 함께 쓴다. 마지막에는 항상 `LOCK_UN`과 fd 닫기.
- 잠금을 쥔 채로 아래 3~4번(마지막 레코드 읽기·genesis·쓰기·`fsync`)을 모두 한다. 모듈 전역 `_sequences`는 없앤다(프로세스 간에 낡는다).

### 3. 마지막 레코드와 이어 쓰기

- 마지막 레코드: 비어 있지 않은 가장 최근 `chain-*.jsonl`(이름 정렬)의 **파일 끝에서** 마지막 줄을 읽는다(전체를 읽지 않는다). 파일의 마지막 바이트가 `\n`이 아니면(개행 없는 꼬리) **append를 거부**한다: `AuditWriteError(code="audit_chain_corrupt")`, 아무것도 덧붙이지 않는다. JSON·필드(`seq`는 `int`, `hash`는 64 hex)가 깨졌어도 같은 코드다.
- 새 레코드: `seq = 마지막.seq + 1`, `prev_hash = 마지막.hash`. **날짜 경계(UTC)가 바뀌어 새 파일이 시작돼도** `prev_hash`·`seq`는 이어진다.
- **genesis**: **비어 있지 않은** `chain-*.jsonl`이 하나도 없을 때(첫 쓰기: 0바이트 파일만 있어도 해당)만, 같은 잠금 안에서 `chain.genesis`를 먼저 쓴 뒤 요청된 레코드를 쓴다. genesis = 시스템 이벤트(`actor.sub="system"`, `task_id=null`), `seq = legacy_last_seq + 1`, `prev_hash = "0" * 64`, 필드 `legacy_files`(legacy 파일 수), `legacy_lines`(총 줄 수), `legacy_last_seq`(legacy 마지막 레코드의 `seq`, 없으면 0), `legacy_sha256` = `sha256(canonical_json([[파일명, 파일바이트sha256], …]))`의 hex(파일명 오름차순, 파일이 없으면 빈 목록의 해시). legacy는 `????-??-??.jsonl` 이름의 파일만이고 줄이 JSON으로 읽히지 않거나 `seq`가 정수가 아니면 `audit_legacy_invalid`로 거부한다(봉인할 수 없다).
- `append`가 돌려주는 값은 요청된 레코드의 `seq`다(genesis의 것이 아니다).

### 4. 테스트 (`tests/test_audit_chain.py`)

- 기본: 레코드마다 `prev_hash` 연결, `hash` 재계산 일치, `sig`가 `load_public_keys()`의 키로 검증됨, 첫 레코드는 genesis(`seq`=1, `prev_hash`=`0`×64, legacy 필드 0·빈 해시), `kid`가 해시 대상에 포함됨(`kid`를 바꾸면 해시가 달라짐), 파일 `0600`·디렉터리 `0700`, 생성자는 키가 없으면 `AuditKeyError`(AUDIT_KEY_FILE을 없는 경로로 바꿔 검증), 시스템 이벤트와 일반 이벤트가 섞여도 연결됨.
- legacy 봉인: 디렉터리에 `2026-10-06.jsonl`(합성 줄 3개, 마지막 `seq`=3)을 미리 두면 genesis의 `legacy_files=1`·`legacy_lines=3`·`legacy_last_seq=3`·`seq=4`이고 `legacy_sha256`이 파일 해시로 계산한 값과 같다. legacy 파일은 바이트 단위로 변하지 않는다. 깨진 legacy → `audit_legacy_invalid`이고 체인 파일이 생기지 않는다.
- 날짜 경계: 가짜 시계로 UTC `23:59:59.9` → 다음 날 `00:00:00.1`을 쓰면 파일이 `chain-…-09.jsonl`·`chain-…-10.jsonl`로 나뉘고 `prev_hash`·`seq`가 이어진다.
- 개행 없는 꼬리: 마지막 줄 끝의 `\n`을 지운 뒤 `append` → `code == "audit_chain_corrupt"`이고 파일 바이트가 변하지 않는다. 비어 있는 최신 파일(0바이트)은 건너뛰고 이전 파일을 잇는다. 0바이트 `chain-*.jsonl`만 있는 디렉터리의 첫 `append`는 genesis를 쓰고 그 뒤 레코드를 잇는다(같은 파일에 이어 쓴다).
- **경합(FP-012·066, 결정론적 불변식)**:
  - 잠금 마감: 부모 프로세스가 디렉터리 fd에 `LOCK_EX`를 쥔 상태에서 `spawn` 자식 프로세스가 `AuditLog(dir, lock_timeout=0.3).append(...)`를 호출하면 `audit_lock_timeout`을 큐로 돌려준다(체인 파일이 생기지 않는다). `multiprocessing.Event`로 부모가 잠금을 풀면 자식의 두 번째 `append`가 성공한다. 모든 `join`·`get`에 `timeout`을 두고 `finally`에서 `terminate`·`join`한다.
  - 동시 쓰기: `spawn` 프로세스 2개가 같은 디렉터리에 각각 N건(N=20)을 쓰면 `seq`가 1부터 연속인 하나의 열이고(genesis 포함 총 2N+1건) `prev_hash`가 모두 이어지며 해시가 모두 재계산과 같다. 순서(어느 프로세스가 먼저인지)는 단언하지 않는다.
  - 스레드: 같은 디렉터리에 `AuditLog` 인스턴스 2개를 스레드 8개가 나눠 쓴 결과도 같은 불변식을 만족한다.
- `append_with_retry`는 `AuditWriteError`(코드와 무관)에서 3회 재시도하고 유지된다. 감사 필드·식별자 검증 오류가 체인 파일을 만들지 않는다(검증은 잠금 전에 한다).
- `tests/test_api_tasks.py`의 동시 승인 테스트(`responses = await asyncio.gather(…)` 뒤의 `assert responses[0].status_code in (200, 202) and responses[1].status_code == 200`): 이 단언은 `gather` 인덱스로 어느 쪽이 202(수락)인지를 고정한다. 제품 동작은 "잠금을 먼저 얻은 쪽 하나가 202, 다른 하나는 같은 결정의 멱등 응답 200"이고 어느 쪽이 먼저인지는 스케줄링에 달려 있으며, 감사 쓰기 타이밍이 바뀌면 `[200, 202]`가 나온다(제품 결함이 아니다, FP-012). 이 단언을 **순서 무관 불변식**으로 바꾼다: 두 응답 코드가 모두 `{200, 202}`에 속하고, `202`는 정확히 1개이고(코드를 정렬하면 `[200, 202]`: gather 전 요청이 모두 승인 없이 끝나 먼저 잠금을 얻은 쪽이 반드시 202를 받는다), 두 응답 본문이 같다. 뒤따르는 단언(`responses[0].json() == responses[1].json()`, 이후 멱등 재요청 두 번, 효과 1건 `len(slack.sent) == 1`)은 **그대로 둔다**. 입력 고정(`monkeypatch`)이나 `sleep`으로 순서를 맞추지 않는다.
- `tests/test_audit_events.py`(step 1 산출물): 신규 이벤트의 허용·거부 표를 실제 `AuditLog`에 돌리는 테스트가 "첫 `append`의 반환값이 1"과 "디렉터리에 `YYYY-MM-DD.jsonl` 한 줄이 이벤트·필드만 담는다"를 가정한다. 첫 `append`는 genesis를 먼저 쓰므로 반환값은 2이고(또는 반환값을 직접 단언하지 않고 마지막 레코드의 `event`·필드로 단언), 저장 파일은 `chain-*.jsonl`이며 마지막 레코드를 읽어 같은 이벤트·필드 값을 단언한다.
- `tests/test_audit.py`와 소비자 테스트: 파일 이름 `????-??-??.jsonl`·레코드 모양을 직접 읽는 단언을 `chain-*.jsonl`·새 필드에 맞게 갱신한다. **레코드에서 필요한 필드만 단언하는 헬퍼가 있으면 그것을 고치고**, 의미(이벤트·필드 값)를 약화하지 않는다.
- `src/govops/audit/__init__.py`: `AuditKeyError`·`SYSTEM_EVENTS`를 내보낸다(기존 내보내기 유지).

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


- 잠금 대기를 시간 제한 없이 걸지 마라. 이유: `flock`에는 마감이 없어 다른 프로세스가 잠금을 쥔 채 멈추면 서비스가 영원히 멈춘다. `LOCK_NB` 재시도와 마감 후 `audit_lock_timeout`이다(FP-066).
- 경합 테스트에서 어느 쪽이 먼저 쓰는지(순서)나 "재시도 횟수 > 0"을 단언하지 마라. 이유: 스케줄링에 달려 CI에서 뒤집힌다. 부모가 잠금을 쥔 상태의 마감 초과와 동시 쓰기의 불변식(`seq` 연속·연결·해시 일치)으로 단언한다(FP-012).
- 프로세스·풀·스레드 대기를 `timeout` 없이 두지 마라. 이유: 멈춘 테스트가 CI를 막는다. `join(timeout=)`과 `finally` 정리를 쓴다(FP-066).
- 선점하지 못했거나 실패한 호출이 기존 레코드·파일을 고치지 않게 하라. 개행 없는 꼬리·마감 초과에서는 파일 바이트가 그대로여야 한다. 이유: 감사 로그는 덧붙이기만 한다. 실패한 호출이 다른 실행의 기록을 건드리면 체인이 깨진다(FP-004의 같은 원칙).
- legacy `????-??-??.jsonl`을 수정·삭제·이동하지 마라. 이유: genesis의 `legacy_sha256`이 현재 파일의 해시와 계속 같아야 한다. 같은 날짜 이름의 체인 파일과 섞이지 않도록 체인은 `chain-` 접두어를 쓴다.
- 느린 디스크·CI 차이를 가정하라: `fsync`를 건너뛰거나 테스트를 `sleep` 길이에 기대지 마라. 이유: 로컬 통과가 CI 통과를 보장하지 않는다(FP-032).
