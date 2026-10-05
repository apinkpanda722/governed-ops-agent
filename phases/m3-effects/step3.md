# Step 3: audit-min

## 읽어야 할 파일

- `/AGENTS.md`, `/docs/ADR.md` (ADR-012), `/docs/ARCHITECTURE.md` ("감사 로그"), `/docs/THREAT_MODEL.md` (V10·V12)
- `/src/govops/audit/__init__.py`, `/src/govops/obs/mask.py`(PII 정규식 재사용 여부 확인), `/src/govops/guardrails/pii.py`, `/tests/test_architecture.py`, `/.gitignore`

## 작업

감사 로그의 **최소 구현**(비체인 JSONL)을 `audit/`에 만든다. 해시체인·서명은 M4가 같은 인터페이스 뒤에 추가한다. 이 step의 목적은 "감사 쓰기에 실패하면 부작용을 실행하지 않는다"를 부작용이 생기기 전에 강제할 수 있게 하는 것이다. TDD로 테스트를 먼저 쓴다.

### 인터페이스 (`src/govops/audit/log.py`, `audit/__init__.py`에서 공개)

```python
class AuditWriteError(Exception): ...
class AuditPort(Protocol):
    def append(self, event: str, *, task_id: str, actor_sub: str, **fields: str | int | bool | None) -> int: ...   # seq 반환, 실패 시 AuditWriteError
class AuditLog:   # AuditPort 구현
    def __init__(self, directory: Path | None = None, *, clock=...): ...
def append_with_retry(sink: AuditPort, event: str, *, attempts: int = 3, **kw) -> bool   # 성공 True, 끝내 실패 False
```

- **이벤트 이름**은 ADR-012 기록 대상 이름을 상수로 둔다: `side_effect.draft`, `approval.requested`, `approval.approved`, `approval.edited`, `approval.rejected`, `approval.expired`, `side_effect.attempt`(실행 시도), `side_effect.result`(결과), `side_effect.marker_lookup`(표식 조회). 목록 밖 이벤트는 `ValueError`(M4가 목록을 확장한다).
- 한 줄 = JSON 하나: `{"seq", "ts", "actor": {"sub": <user>, "act": "governed-ops-agent"}, "event", "task_id", ...fields}`. 키 정렬·UTF-8.
- **원문 금지(형식 검증)**: 필드 값은 아래 형식 중 하나에 맞아야 한다. 맞지 않으면 `ValueError`로 거부한다.
  - 해시: `^[0-9a-f]{64}$`
  - mock ID: 정해진 형식(`message-<n>`, 티켓 ID `^[A-Z]{3}-\d+$`, 코멘트 ID 등 mock이 만드는 형식을 `mocks`를 import하지 않고 코드 상수 정규식으로 둔다. 실제 형식은 `mocks/slack.py`·`mocks/jira.py`에서 확인한다)
  - 결정 코드·도구 이름·`reason` 코드: 코드 상수 **허용 목록**에 있는 값(도구 이름은 부작용 3종, 결정 코드는 `approve`·`edit_approve`·`reject`·`expired` 등, 이유 코드는 이 phase에서 쓰는 것만)
  - 정수·불리언·`None`
  - `text`·`body`·`args`·`content`·`description`·`title` 같은 이름의 필드는 값과 무관하게 거부한다.
  - **개인정보 정규식(`guardrails/pii`)은 위 형식에 맞지 않는 문자열에만 돌린다.** 해시·mock ID에는 돌리지 않는다. 이유: 무작위 sha256 hex의 약 1%가 카드번호 규칙(13자리 이상 연속 숫자 + Luhn)에 걸려 정상 의도 기록이 무작위로 거부된다. 형식 밖 문자열은 PII가 섞여 있으면 거부하고, 섞여 있지 않아도 허용 목록 밖이므로 거부한다(즉 형식 밖 값은 항상 거부이며, PII 검사는 거부 사유를 구분하는 용도다).
- 저장: 기본 디렉토리 `data/interim/audit/`(환경 변수 `AUDIT_DIR`로 재정의 가능, `.env.example`에는 이름만 추가), 날짜별 파일 `YYYY-MM-DD.jsonl`, 디렉토리 0700·파일 0600. 날짜 파일이 바뀌어도 `seq`는 이어진다(시작 시 마지막 줄의 seq에서 이어 간다). 프로세스 안 락으로 동시 append를 직렬화한다.
- 쓰기 실패(디스크 오류·권한 오류·잘못된 필드)는 모두 `AuditWriteError`다(원인 예외를 체인으로 노출하지 않고 안전한 메시지만).
- `append_with_retry`: `AuditWriteError`면 최대 `attempts`회 재시도하고 끝내 실패하면 `False`. 호출자(다음 step의 실행기)가 `audit_gap`을 표시한다. 실행 **전 의도** 기록은 재시도 없이 실패를 그대로 올려 부작용을 막는다.

### 테스트

- seq 단조 증가, 재오픈·날짜 변경 후에도 이어짐, 동시 append(스레드) seq 중복 없음.
- 필드 검증: 금지 이름 거부, 모든 이벤트 이름 허용·그 밖 거부. **무작위 sha256 hex 10,000개(시드 고정)가 모두 통과**하고, 형식 밖 문자열(주민번호·전화·카드번호·이메일이 섞인 값 포함)은 거부된다. 허용 목록 밖의 도구 이름·결정 코드는 거부된다.
- 쓰기 실패 주입(읽기 전용 디렉토리 등) → `AuditWriteError`, `append_with_retry`는 재시도 후 `False`.
- 쓴 파일 전체에서 허용 형식 밖의 값 0건, 파일 권한 0600.
- 계층 가드: `audit/`는 `agent`·`tools`·`broker`·`api`를 import하지 않는다.

## Acceptance Criteria

```bash
make verify   # ruff check + ruff format --check + pytest + web 검사
```

## 검증 절차

1. 위 AC 커맨드를 실행한다.
2. 아키텍처 체크리스트를 확인한다:
   - ARCHITECTURE.md의 감사 로그 형식(seq·ts·actor·event·task_id)을 따르는가? 감사 로그 쓰기 지점이 `audit/` 한 곳인가?
   - 원문 대신 해시·mock ID만 기록하는가?
   - AGENTS.md CRITICAL 규칙을 위반하지 않았는가?
3. 결과를 최종 응답(JSON)으로 보고한다. `phases/` 아래 파일은 수정하지 않고 git commit도 하지 않는다:
   - 성공 → `"status": "completed"`, `"summary": "산출물 한 줄 요약"`
   - 해결할 수 없는 실패 → `"status": "error"`, `"reason": "구체적 에러 내용"`
   - 사용자 개입 필요 → `"status": "blocked"`, `"reason": "구체적 사유"` 후 즉시 중단

## 금지사항

- `prev_hash`·`hash`·`sig` 필드를 만들거나 서명 키를 다루지 마라. 이유: 해시체인·서명은 M4 범위이고 개인키 생성은 사용자 작업이다.
- 본문·인자 원문을 감사 로그에 쓰지 마라. 이유: 지울 수 없는 로그에 PII를 넣으면 삭제 요구에 응할 수 없다(ADR-012).
- 감사 로그를 쓰는 코드를 `audit/` 밖에 만들지 마라. 이유: 쓰기 지점은 한 곳이다.
- 실행 전 의도 기록 실패를 삼키지 마라. 이유: 의도를 못 남기면 부작용을 실행하지 않는다(fail-closed).
- `data/interim/audit/`의 파일을 커밋하지 마라(gitignore 대상 확인).
- `scripts/`, `phases/`, `web/`, `data/contract/`, `data/eval/`을 수정하지 마라.
- `.env`와 A의 `data/interim/mcp/tokens.json`을 열거나 출력하지 마라.
- 기존 테스트를 깨뜨리지 마라. 계층 가드를 약화하지 마라.
