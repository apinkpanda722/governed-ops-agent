# Step 1: overlay-compose

## 읽어야 할 파일

먼저 아래 파일들을 읽고 프로젝트의 아키텍처와 설계 의도를 파악하라:

- `/AGENTS.md`(계층 경계, CRITICAL 규칙)
- `/docs/ARCHITECTURE.md`, `/docs/ADR.md`, `/docs/THREAT_MODEL.md`
- `/docs/M6_REDTEAM_RUN_DESIGN_DRAFT.md`(이 phase의 설계), `/docs/results/hypotheses-m6-redteam-run.md`(사전 등록, 읽기만 한다)
- `/.claude/skills/failure-patterns/SKILL.md` — 읽기만 한다
- `/infra/docker-compose.yml`(`mock-jira` 서비스 — 읽기만 한다), `/src/govops/mocks/__main__.py`(`MOCK_JIRA_OVERLAY`), `/src/govops/mocks/seed.py`(`merge_overlay`), `/src/govops/mocks/jira.py`(`_response`)
- `/src/govops/evalrun/dev.py`(`mock_seed_check`, `read_seed_matches` — 읽기만 한다, 수정하지 않는다), `/src/govops/evalrun/mock_admin.py`, `/src/govops/evalrun/preflight.py`(`mock_images`)
- `/tests/test_compose.py`, `/tests/test_evalrun_dev.py`(시드 검사 테스트 방식)

이전 step에서 만들어진 코드를 꼼꼼히 읽고, 설계 의도를 이해한 뒤 작업하라.

## 작업

**수정할 파일**(이 목록이 전부다): `infra/docker-compose.redteam.yml`(신규), `src/govops/evalrun/overlay_env.py`(신규), `tests/test_evalrun_overlay_env.py`(신규), `tests/test_compose_redteam_override.py`(신규). TDD.

열람 금지 경로: `data/interim/`, `data/redteam/`, `data/eval/`, `src/govops/guardrails/rules_v1.json`
수정 허용 경로: `infra/docker-compose.redteam.yml`, `src/govops/evalrun/overlay_env.py`, `tests/test_evalrun_overlay_env.py`, `tests/test_compose_redteam_override.py`

열람 금지의 범위: 이 금지는 **Codex 세션이 위 경로의 파일 내용을 직접 열람·출력·인용하는 것**에 대한 것이다. 이 step의 테스트는 위 경로의 실제 파일을 읽지 않고 임시 폴더에 만든 합성 파일만 쓴다. 코드가 실행 중에 `data/redteam/`·`data/eval/`를 읽는 것은 사용자 실행 시의 런타임 접근이므로 이 금지의 대상이 아니다(이 step에서 그 경로로 코드를 실행하지 않는다). 구조는 이 문서·등록 파일·기존 코드와 테스트로 파악한다.

**소비자 점검 대상**(**~에 한함, 단언 약화 금지**): 이 step은 새 파일만 만든다. 변경을 시뮬레이션한 전체 `pytest`(`git archive HEAD` 복사본, `.env`·`data/interim`·`data/raw` 미복사)에서 새로 깨진 기존 테스트는 없었다. 소비자 점검 결과(시뮬레이션에서 통과, 수정 불필요): `tests/test_architecture.py`, `tests/test_makefile.py`, `tests/test_compose.py`, `tests/test_docs_consistency.py`. 이 단락 밖의 기존 테스트는 수정하지 않는다. 기존 테스트를 수정했다면 최종 응답 summary에 파일명과 깨진 단언 이름을 적는다.

**배경**: 레드팀 간접 인젝션 40건의 오버레이 티켓(`data/redteam/overlay/jira_tickets.json`)이 mock-jira에 들어가려면 `MOCK_JIRA_OVERLAY` 환경 변수와 마운트가 필요한데 기본 compose에는 둘 다 없다. 기본 compose와 기존 시드 검사를 바꾸지 않고 **별도 오버라이드 파일**로 연결한다. 기존 `mock_seed_check`는 `ticket_count == len(기본 시드)`를 기대하므로 오버레이 구성에서는 별도 검사가 필요하다.

### 1. `infra/docker-compose.redteam.yml`

- 서비스 `mock-jira`만 정의한다: `volumes`에 `../data/redteam:/app/data/redteam:ro`, `environment`에 `MOCK_JIRA_OVERLAY: /app/data/redteam/overlay/jira_tickets.json`. 다른 서비스·포트·`build`·`command`는 쓰지 않는다(기본 파일과 병합된다).
- 최상위 키는 `services` 하나다. 헤더 주석에 사용법(`docker compose --env-file .env -f infra/docker-compose.yml -f infra/docker-compose.redteam.yml up -d`)과 "레드팀 실행 전용, 일반 평가는 쓰지 않는다"를 적는다.

### 2. `src/govops/evalrun/overlay_env.py`

- `OVERLAY_RELATIVE = "data/redteam/overlay/jira_tickets.json"`.
- `overlay_ticket_ids(root) -> list[str]`: 오버레이 JSON의 `tickets[*].ticket_id`(런타임 읽기). 형식이 다르면 `ValueError("overlay_mismatch")`.
- `overlay_seed_check(root, *, runner=subprocess.run) -> bool`: `evalrun.dev.mock_seed_check`와 같은 방식(`docker compose --env-file .env -f infra/docker-compose.yml [-f infra/docker-compose.redteam.yml] exec -T <service> python -m govops.mocks.admin state`)으로 mock-jira·mock-slack 상태를 읽어 기대값과 비교한다. 기대값은 같은 구조에서 **`ticket_count = len(기본 시드 티켓) + len(오버레이 티켓)`**, 코멘트·후속 티켓·채널 메시지 0이다. 비교 대상 키·형식은 `mock_seed_check` 구현을 읽고 그대로 따른다. `-f` 오버라이드 인자를 명령에 포함한다. 실패하면 `False`(예외 문장에 출력 본문을 넣지 않는다).
- `read_overlay_matches(jira, base_tickets, overlay_tickets, users) -> bool`(async): 기본 시드 티켓은 `evalrun.dev.read_seed_matches`와 같은 규칙으로, 오버레이 티켓은 `reporter_id` 라벨 사용자로 `jira.get_ticket`을 불러 읽을 수 있고 `description`·`comments`가 일치하는지 확인한다(오버레이 티켓은 `comments`·`children` 키를 가진다 — `mocks/jira.py::_response` 참고). 티켓 ID 집합이 기본 시드 ∪ 오버레이와 정확히 같아야 한다. 내용을 출력하지 않는다.
- 이 모듈은 `evalrun.dev`의 함수를 **수정하지 않는다**(import해 호출만 할 수 있다).

### 테스트

- **`tests/test_compose_redteam_override.py`**: 오버라이드 파일을 YAML로 읽어 최상위 키가 `services` 하나, 서비스는 `mock-jira` 하나, `volumes`가 정확히 `["../data/redteam:/app/data/redteam:ro"]`, `environment`가 정확히 `{"MOCK_JIRA_OVERLAY": "/app/data/redteam/overlay/jira_tickets.json"}`임을 단언한다. 기본 `infra/docker-compose.yml` 텍스트에 `MOCK_JIRA_OVERLAY`와 `data/redteam`이 **없음**을 단언한다(기본 구성 불변). 병합 의미 확인: 오버라이드의 환경 변수 경로가 마운트 경로 아래다.
- **`tests/test_evalrun_overlay_env.py`**(가짜 `runner`·가짜 jira, 합성 임시 파일): ① `overlay_ticket_ids` 정상·형식 오류 `overlay_mismatch`. ② `overlay_seed_check`: 가짜 runner가 `ticket_count == base + overlay`를 돌려주면 True, `== base`만 돌려주면(오버레이 미연결) False, 명령에 `-f infra/docker-compose.redteam.yml`이 포함됨, 반환 코드 실패·잘못된 JSON은 False. ③ `read_overlay_matches`: 기본·오버레이 티켓이 모두 맞으면 True, 오버레이 티켓 하나가 읽히지 않거나 내용이 다르면 False, 기본 시드에 없는 여분 ID가 있으면 False. ④ 기존 `evalrun.dev.mock_seed_check`의 소스가 바뀌지 않았음(이 step이 해당 파일을 수정하지 않음)은 `git diff` 대신 단언하지 않는다 — 기존 `tests/test_evalrun_dev.py`가 수정 없이 통과해야 한다.

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

- `infra/docker-compose.yml`을 수정하지 마라. 이유: 기본 스택과 다른 평가(`mock_seed_check`의 티켓 수 단언, dev 20 시드 검사)가 바뀌지 않아야 한다(결정 9).
- `evalrun/dev.py`의 `mock_seed_check`·`read_seed_matches`를 수정하지 마라. 이유: 기존 dev·장애 주입 러너와 테스트가 의존한다.
- 오버라이드 파일에 mock-slack·포트·이미지·네트워크를 정의하지 마라. 이유: mock 관리 엔드포인트는 compose 내부 네트워크에만 연다(AGENTS.md CRITICAL).
- 오버레이 티켓의 본문(공격 문장)을 테스트·로그·출력에 옮기지 마라. 이유: `data/redteam/` 원문이며 열람 금지 경로다.
- `docker compose`를 실행하지 마라. 이유: 실제 서비스에 접속하는 사용자 실행 항목이다. 가짜 `runner`로만 테스트한다.
