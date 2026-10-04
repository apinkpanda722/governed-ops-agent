# Step 9: docs-sync

## 읽어야 할 파일

먼저 아래 파일들을 읽고 프로젝트의 아키텍처와 설계 의도를 파악하라:

- `/AGENTS.md`, `/docs/PRD.md`, `/docs/ARCHITECTURE.md`, `/docs/ADR.md`, `/docs/EVAL.md`, `/docs/THREAT_MODEL.md` (전체)
- 이 phase(`m1-tools`)의 산출물: `/src/govops/mocks/`, `/src/govops/broker/`, `/src/govops/replay/`, `/src/govops/tools/`, `/data/contract/manifest.json`, `/Makefile`, `/.env.example`, `/infra/docker-compose.yml`, `/tests/`
- 이전 step(0~8)의 summary(프롬프트로 전달된 컨텍스트)

## 작업

이 phase에서 확정된 결정과 마일스톤 조정을 문서에 반영한다. **코드는 수정하지 않는다.** 문서에는 **결정 내용만** 쓰고 변경 경위·논의 과정은 쓰지 않는다. ADR 본문의 기존 "변경 기록" 항목에는 줄을 추가하지 않는다(새 ADR-017의 변경 기록만 예외로 아래 지정대로).

### 1. ADR-017 신설 (`docs/ADR.md` 끝에 추가, 기존 형식을 따른다: 상태·결정·이유·트레이드오프·변경 기록)

제목: `ADR-017: M1 결정 — 도구 이름, mock 자격 증명, B 세션 토큰, checkpointer DB`. 상태 유효. 결정 4건:
1. **도구 이름**: `search_docs`, `lookup_id`, `run_sql`, `get_document`(A 계약), `jira_get_ticket`, `jira_search_tickets`(읽기), `jira_add_comment`, `jira_create_followup_ticket`(필수 `parent_ticket_id`), `slack_send_message`(부작용). 형식은 `{시스템}_{동사}_{대상}` snake_case. 입력 스키마에 사용자·팀·직급 필드 없음. 채널 이력·코멘트 목록·후속 티켓 목록 조회는 표식 조회용으로 도구 계층만 쓰고 모델에 노출하지 않는다. mock-slack 이력 조회는 자기 팀 채널·전사 공지만, 그 밖은 403.
2. **mock 사용자 자격 증명**: 사용자 38명 × audience(`mock-jira`, `mock-slack`)별 토큰. 평문은 `data/interim/mock/tokens.json`(0600, 커밋 안 함), 해시 매니페스트(`data/seed/mock_jira_token_hashes.json`, `data/seed/mock_slack_token_hashes.json`)와 사용자 목록 `data/seed/users.json`(label·team·grade)은 커밋. mock이 토큰 해시로 인증해 team·grade를 정한다. broker가 평문 파일을 읽는 유일한 곳(`MOCK_TOKENS_FILE` 환경 변수, 기본 위 경로).
3. **B 세션 토큰**: `POST /sessions`가 `DEMO_LOGIN` 플래그이고 127.0.0.1 클라이언트일 때만 열린다. 토큰은 `secrets.token_urlsafe(32)`이고 서버는 SHA-256 해시만 메모리에 두며 `user_id`·만료 시각에 매핑한다(재시작 시 재로그인). TTL은 임시 상수이고 최종값은 M5에서 정한다. 인증 의존성이 `Authorization: Bearer`에서 `user_id`를 얻고 승인자 신원도 같은 경로다. 다른 사용자의 task 조회는 404.
4. **checkpointer DB**: 같은 Postgres 인스턴스에 새 DB `govops`(앱 DB). checkpointer 테이블을 두고 M3 outbox 저장 위치는 여기서 정하지 않는다. `infra/postgres/init/01-create-databases.sh`에 추가하고 기존 볼륨용 멱등 명령 `make db-init`을 둔다. `GOVOPS_DATABASE_URL` 환경 변수. `make verify`는 Postgres 없이 통과해야 하므로 테스트는 in-memory saver를 쓰고 실제 Postgres는 스모크로 확인한다.

변경 기록: `2026-10-04: M1 설계에서 확정.` 한 줄.

### 2. 마일스톤 조정 반영 (`docs/PRD.md`, `docs/ADR.md`의 ADR-011·ADR-013, 그 밖의 문서)

- **승인 노드 핵심(`interrupt`, 초안 해시 결합, `POST /tasks/{id}/approval`)은 M3**로 둔다. 이유(문서에는 사실로만): M3 장애 주입의 중복 0건 검증에 부작용 실행 경로가 필요하고, 그 경로는 승인을 거친다.
- **M5**에는 웹 화면(로그인·요청 진행·승인함), 전사 공지 승인자 정책, MAX_STEPS/COST, 탐색 예산(ADR-014), checkpointer 보존 기간 삭제(ADR-015)를 남긴다.
- `docs/PRD.md` 마일스톤 표: M1 행은 두 phase(`m1-tools`, `m1-agent`)로 진행한다고 표기하고, M3 행 산출물에 승인 노드 핵심을 추가하며 M5 행에서 제거한다(측정·사용자 작업 열도 맞춘다: M3 측정에 "재개 반복 → 부작용 1회", M5 측정에서 이를 빼고 "루프 차단 동작" 등 남는 항목만). **M2 행의 사용자 작업 열에 한 줄을 추가한다: "Langfuse 기동 전 Redis 7.4.11 이후 보안 수정 여부 재확인".**
- `docs/ADR.md`: ADR-013, ADR-011, 그리고 마일스톤을 언급하는 다른 ADR(ADR-014·ADR-015 등)의 본문을 위 기준으로 맞춘다. `docs/ARCHITECTURE.md`, `docs/EVAL.md`, `docs/THREAT_MODEL.md`, `AGENTS.md`에서 `M3`·`M5`를 언급하며 승인 노드·HITL의 구현 시점을 말하는 문장도 `grep -n "M3\|M5"`로 찾아 같은 기준으로 고친다. M5에 남는 항목(웹 화면, 전사 공지 승인자 정책, MAX_STEPS/COST, 탐색 예산, 보존 기간 삭제, 쿠키 속성·CSRF·세션 만료)의 언급은 M5로 유지한다.

### 3. 확정된 "미정"의 갱신

- `docs/ARCHITECTURE.md`: "디렉토리 구조"의 `data/` 하위 이름 미정 문구를 확정 이름(`data/contract/`, `data/seed/`, `data/replay/`)으로 바꾸고, `data/seed/`·`data/replay/`에 실제로 들어가는 파일(`tickets.json`, `users.json`, mock 해시 매니페스트 2개, manifest, 기록 `recordings.jsonl`, 요청 `requests.json`)을 적는다. "도구와 부작용·멱등성"의 "도구 이름 … 미정" 문장에서 도구 이름 부분을 확정으로 바꾸고(표식 값 형식·대기 시간·outbox 저장소는 계속 미정 M3), 표의 도구 칸에 확정 이름을 쓴다. "자격 증명 중개 흐름"의 mock 자격 증명 발급 미정 문장과 "배치도"·"상태 관리"의 checkpointer DB 위치 미정 문장을 ADR-017 결정으로 바꾼다. mock 관리 CLI에 `state`·`reset` 명령이 생겼음을 반영한다(장애 주입 명령은 M3 미정 유지).
- `docs/ADR.md` ADR-016의 "checkpointer DB 위치는 미정"이라는 문장은 본문에서 ADR-017로 확정되었다고 짧게 가리킨다(ADR-016 변경 기록에는 줄을 추가하지 않는다).
- `AGENTS.md` "명령어" 섹션에 이 phase에서 생긴 make 타깃(`mock-tokens`, `seed-extract`, `record-a`, `smoke-contract`, `smoke-identity`, `smoke-visibility`, `smoke-mock`, `smoke-host-header`)을 한 줄씩 추가한다. **CRITICAL 규칙·계층 경계 표·기술 스택은 그대로 둔다.** `docs/ADR.md` ADR-004의 "확인 필요(Host 헤더 실측)"는 사용자가 `make smoke-host-header`로 실측하므로 유지한다.
- 이 phase에서 만든 모듈·환경 변수(`MOCK_TOKENS_FILE`, `MOCK_JIRA_URL`, `MOCK_SLACK_URL`, `MOCK_JIRA_TOKEN_HASHES`, `MOCK_SLACK_TOKEN_HASHES`, `MOCK_JIRA_TICKETS`)의 용도가 문서와 다르게 적힌 곳이 없는지 확인하고, `.env.example`에는 사용자가 설정해야 하는 이름만 있는지 확인한다(코드로 고정된 기본값 변수는 추가하지 않는다).

### 4. 검증용 테스트

- 문서 변경을 검사하는 테스트를 `tests/test_docs_consistency.py`로 추가한다: ADR-017이 존재하고 도구 9개 이름이 ARCHITECTURE의 도구 표와 `src/govops/tools/registry.py`의 `tool_specs()` 이름과 일치한다. PRD의 M3 행에 `approval` 또는 `승인`이 있고 M5 행에는 `interrupt`·`approval` 승인 노드 구현 문구가 없다. AGENTS.md의 CRITICAL 항목 수가 이 step 이전과 같다(앞뒤로 센 값 비교 대신 현재 개수를 상수로 고정). ARCHITECTURE의 `data/` 구조에 `contract/`·`seed/`·`replay/`가 있다.

## Acceptance Criteria

```bash
make verify   # ruff check + ruff format --check + pytest + web 검사
```

## 검증 절차

1. 위 AC 커맨드를 실행한다.
2. 체크리스트를 확인한다:
   - AGENTS.md의 CRITICAL 규칙·계층 경계 표·기술 스택이 변경되지 않았는가?
   - 문서에 변경 경위·논의 서술이 없고 결정 내용만 있는가? ADR 기존 변경 기록에 줄이 추가되지 않았는가?
   - 승인 노드 핵심이 M3, 나머지 HITL 관련 항목이 M5로 일관되게 적혔는가? M2 사용자 작업에 Redis 재확인 한 줄이 있는가?
3. 결과를 최종 응답(JSON)으로 보고한다. `phases/` 아래 파일은 수정하지 않고 git commit도 하지 않는다:
   - 성공 → `"status": "completed"`, `"summary": "산출물 한 줄 요약"`
   - 해결할 수 없는 실패 → `"status": "error"`, `"reason": "구체적 에러 내용"`
   - 사용자 개입 필요 → `"status": "blocked"`, `"reason": "구체적 사유"` 후 즉시 중단

## 금지사항

- `src/`, `tests/`(새 문서 일관성 테스트 제외), `infra/`, `Makefile`, `data/`의 코드·데이터를 수정하지 마라. 이유: 이 step은 문서 동기화만 한다.
- AGENTS.md의 CRITICAL 규칙·계층 경계 표를 약화하거나 삭제하지 마라. 이유: 규칙의 단일 원본이다.
- 변경 경위·논의("~에서 ~로 바뀌었다", "사용자가 요청해서")를 문서에 쓰지 마라. 이유: 문서는 현재 결정만 적는다.
- 기존 ADR의 "변경 기록"에 줄을 추가하지 마라(ADR-017 신설분 제외).
- 확정되지 않은 것(표식 값 형식, 2단계 조회 대기 시간, outbox 저장소, 쿠키 속성·CSRF·세션 만료, 탐색 예산 N, MAX_STEPS/COST 값, 장애 주입 CLI 명령)을 확정으로 적지 마라. 이유: 각 마일스톤에서 정한다.
- docker, 설치·네트워크 명령을 실행하지 마라. `.env`와 A의 `data/interim/mcp/tokens.json`을 열거나 출력하지 마라. A 저장소를 수정하지 마라.
- `scripts/`, `phases/`, `web/`, `data/contract/`를 수정하지 마라.
- 기존 테스트를 깨뜨리지 마라.
