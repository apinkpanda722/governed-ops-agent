# Step 3: seed-extract

## 읽어야 할 파일

먼저 아래 파일들을 읽고 프로젝트의 아키텍처와 설계 의도를 파악하라:

- `/AGENTS.md` (고정 제약: 합성 기록·실존 이름 금지, A 저장소 수정 금지, `data/raw`·`data/interim` 커밋 금지)
- `/docs/ARCHITECTURE.md` ("디렉토리 구조"의 `data/seed/`, "A 연동 모드"의 정답 출처 항목, "도구와 부작용·멱등성"의 mock-jira 권한 항목)
- `/docs/ADR.md` (ADR-006 시드 항목)
- step 1 산출물: `/src/govops/mocks/seed.py`(티켓 시드 형식 `TICKET_FIELDS`·`load_tickets`), `/src/govops/mocks/auth.py`, `/src/govops/mocks/tokens.py`
- 참고(읽기 전용, A 저장소, 이 스크립트가 읽는 원천):
  - `/Users/taserlee/PycharmProjects/ko-docs-agentic-rag/data/synthetic/ledger.sqlite`의 `tickets` 테이블(컬럼: ticket_id, project_key, owner_team, title, ticket_type, status, priority, service, incident_id, reporter_id, assignee_id, created_at, resolved_at, story_points, access_level, access_teams(쉼표 구분 문자열, public이면 빈 문자열), access_min_grade, is_anchor, fact_ids)
  - `/Users/taserlee/PycharmProjects/ko-docs-agentic-rag/data/manifest/mcp_token_hashes.json` (`{"users": [{"label", "team", "grade", "token_sha256"}]}`, 해시만 있는 파일)
  - `/Users/taserlee/PycharmProjects/ko-docs-agentic-rag/data/seed/banned_names.yaml` (`{"names": [...]}` 형식의 금지 이름 목록)

## 작업

A 저장소의 합성 티켓·사용자 정보를 B의 `data/seed/`로 **일회성 추출**하는 스크립트를 만든다. 스크립트는 사용자가 실행한다. 이 step에서는 임시 디렉토리의 가짜 A 트리로 테스트만 한다. TDD로 테스트를 먼저 쓴다.

### 만들 파일: `src/govops/mocks/seed_extract.py` (`python -m govops.mocks.seed_extract`)

```python
def extract(a_root: Path, out_dir: Path, *, commit_resolver: Callable[[Path], str] | None = None) -> ExtractReport
def main(argv: list[str] | None = None) -> int
# 옵션: --a-root (필수), --out (기본 data/seed), --expected-a-commit (기본 d2eec9c)
```

출력 파일(모두 `out_dir`):
1. `tickets.json`: `{"tickets": [...]}`. step 1의 `load_tickets` 형식을 따른다. ticket_id 오름차순. `access_teams`는 쉼표 문자열을 리스트로 바꾼다(빈 문자열 → `[]`). `is_anchor`·`fact_ids`는 넣지 않는다.
2. `users.json`: `{"users": [{"label", "team", "grade"}...]}`. A의 `mcp_token_hashes.json`에서 **label·team·grade 세 필드만** 복사한다. 해시는 절대 복사하지 않는다. label 오름차순.
3. `manifest.json`: `{"a_commit": <A 전체 커밋 해시>, "expected_a_commit": <옵션 값>, "a_commit_matches_expected": bool, "ticket_count", "user_count", "tickets_sha256", "users_sha256"}`. A 커밋은 `git -C <a_root> rev-parse HEAD`로 구한다(테스트에서는 `commit_resolver` 주입). 기준 커밋과 다르면 **경고만 출력하고 계속한다**(AGENTS.md: 다르면 경고).

핵심 규칙:
- A 저장소는 **읽기 전용**이다. sqlite는 `file:<경로>?mode=ro` URI(`uri=True`)로 열어 쓰기·WAL 생성이 일어나지 않게 한다. A 트리에 어떤 파일도 만들거나 바꾸지 않는다.
- 검증: ticket_id가 `^[A-Z]{3}-\d+$`, reporter_id·assignee_id가 `^p-\d+$`(사람 이름이 아니라 ID만 쓴다), `access_level`이 `public|team|restricted`, 팀 ID가 알려진 8개(`platform, payments, data-ai, sre, security, hr, finance, legal`) 중 하나. 위반이 있으면 파일을 쓰지 않고 오류로 종료한다.
- **실존 이름 금지**: `banned_names.yaml`의 이름이 티켓 제목에 하나라도 포함되면 파일을 쓰지 않고 오류로 종료한다(오류 메시지에는 ticket_id만 쓰고 제목 전문은 넣지 않는다).
- 출력은 결정론이다(키 정렬 JSON, UTF-8, 줄바꿈 종료). 같은 입력으로 두 번 실행하면 바이트가 같다.
- 출력 도중 실패해도 반쯤 쓴 파일이 남지 않게 임시 파일에 쓴 뒤 교체한다.
- 콘솔에는 개수, A 커밋 앞 12자리, 경고만 출력한다.

### 테스트 (`tests/test_seed_extract.py`)

- 임시 디렉토리에 가짜 A 트리를 만든다: 작은 `ledger.sqlite`(tickets 5~6건: public·team·restricted 혼합), `mcp_token_hashes.json`(사용자 3명), `banned_names.yaml`. 커밋은 `commit_resolver`로 주입한다.
- 정상 추출: `tickets.json`이 step 1의 `load_tickets`로 읽히고 개수·정렬·`access_teams` 리스트 변환이 맞다. `users.json`에 `token_sha256`이라는 문자열이 없다(파일 전체 검색). manifest의 개수·해시가 맞다.
- 두 번 실행한 출력이 바이트 동일.
- 기준 커밋과 다를 때 경고(`capsys`)를 출력하고 성공한다.
- 실패 경로: 금지 이름이 제목에 있음, ticket_id 형식 위반, 알 수 없는 팀, 사람 이름이 reporter_id에 들어 있음 → 종료 코드 비정상이고 **출력 파일이 하나도 생기지 않는다**.
- A 트리 불변: 실행 전후로 가짜 A 트리의 모든 파일의 내용 해시와 파일 목록이 같다(sqlite 옆에 `-wal`·`-journal` 파일이 생기지 않는다).
- 저장소의 `data/seed/`에 파일이 이미 있으면 그 파일들이 `load_tickets`와 위 검증을 통과한다는 테스트를 둔다(파일이 없으면 건너뛴다. 이 step에서는 파일이 없다).

## Acceptance Criteria

```bash
make verify   # ruff check + ruff format --check + pytest + web 검사
```

## 검증 절차

1. 위 AC 커맨드를 실행한다.
2. 아키텍처 체크리스트를 확인한다:
   - `govops.mocks`가 다른 govops 계층을 import하지 않는가?
   - A 저장소를 어떤 방식으로도 수정하지 않는가? 토큰 해시를 복사하지 않는가?
   - 이름 대신 ID만 남기고 금지 이름 검사를 강제하는가?
   - AGENTS.md CRITICAL 규칙을 위반하지 않았는가?
3. 결과를 최종 응답(JSON)으로 보고한다. `phases/` 아래 파일은 수정하지 않고 git commit도 하지 않는다:
   - 성공 → `"status": "completed"`, `"summary": "산출물 한 줄 요약"`
   - 해결할 수 없는 실패 → `"status": "error"`, `"reason": "구체적 에러 내용"`
   - 사용자 개입 필요 → `"status": "blocked"`, `"reason": "구체적 사유"` 후 즉시 중단

## 금지사항

- 이 step에서 실제 A 저장소를 대상으로 스크립트를 실행하지 마라. 이유: 추출은 사용자가 실행한다. 테스트는 임시 가짜 트리만 쓴다.
- `data/seed/`에 시드 파일을 만들지 마라. 이유: 사용자가 실행 결과를 검토한 뒤 커밋한다.
- A의 `mcp_token_hashes.json`에서 `token_sha256`을 복사하거나 출력하지 마라. 이유: 해시도 B에 둘 이유가 없다. B의 mock은 자체 토큰을 쓴다.
- sqlite를 쓰기 모드로 열지 마라. 이유: A 저장소를 수정하지 않는다(AGENTS.md CRITICAL).
- `Makefile`을 수정하지 마라. 이유: make 타깃은 step 8에서 추가한다.
- docker, 설치·네트워크 명령을 실행하지 마라. `.env`와 A의 `data/interim/mcp/tokens.json`을 열거나 출력하지 마라.
- `scripts/`, `phases/`, `web/`, `data/contract/`를 수정하지 마라.
- 기존 테스트를 깨뜨리지 마라.
