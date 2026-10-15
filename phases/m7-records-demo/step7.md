# Step 7: demo-scripts

## 읽어야 할 파일

먼저 아래 파일들을 읽고 프로젝트의 아키텍처와 설계 의도를 파악하라:

- `/AGENTS.md`(계층 경계, CRITICAL 규칙)
- `/docs/ARCHITECTURE.md`, `/docs/ADR.md`, `/docs/THREAT_MODEL.md`
- `/docs/M7_DESIGN_DRAFT.md`(이 phase의 승인된 설계), `/docs/results/hypotheses-m7-records-authz.md`(사전 등록, 읽기만 한다)
- `/.claude/skills/failure-patterns/SKILL.md` — 읽기만 한다
- `/src/govops/evalrun/faults_smoke.py`(`WRITE_RULES` 형식), `/src/govops/evalrun/mock_admin.py`(stdin 규칙 전달)
- `/src/govops/audit/verify.py`(CLI 옵션 `--dir`·`--anchors`·`--repo-root`)
- `/Makefile`, `/tests/test_makefile.py`

이전 step에서 만들어진 코드를 꼼꼼히 읽고, 설계 의도를 이해한 뒤 작업하라.

## 작업

**수정할 파일**(이 목록이 전부다): `scripts/demo/scene5.sh`, `scripts/demo/mock_faults.json`, `Makefile`, `tests/test_demo_scripts.py`. TDD.

열람 금지 경로: `data/interim/`
수정 허용 경로: `scripts/demo/scene5.sh`, `scripts/demo/mock_faults.json`, `Makefile`, `tests/test_demo_scripts.py`

열람 금지의 범위: 이 금지는 **Codex 세션이 해당 경로의 파일을 직접 열람·출력·인용하는 것**에 대한 것이다. 이 step의 코드와 테스트가 런타임에 그 경로를 다루는 것은 대상이 아니다. 단 테스트가 `data/interim/`의 실제 파일 **내용**을 읽는 것은 여전히 금지이며 테스트는 임시 디렉터리·합성 데이터·테스트용 키(`tests/conftest.py`의 `isolated_audit_environment`)만 쓴다. 기본 경로 문자열(예: `AUDIT_DIR`의 기본값)이 코드에 있는 것은 대상이 아니다.

**배경**: 데모 보조 스크립트와 `make` 타깃이다. 스크립트는 사용자가 실행하며 이 step에서 실행하지 않는다.

### 1. `scripts/demo/mock_faults.json`

`faults_smoke.WRITE_RULES`와 같은 형식: `slack.send` `nth [1]`에 `drop_after_apply`와 `hide_for`(`ms` 1000) 규칙 두 개의 JSON 배열.

### 2. `scripts/demo/scene5.sh` (POSIX sh, `set -eu`)

1. `mktemp -d`로 저장소 안 `data/interim/` 아래(gitignore 대상이라 작업 트리가 더러워지지 않는다, FP-067)에 임시 작업 디렉터리를 만들고 `trap`에서 `mktemp -d`가 돌려준 변수(`work`) 경로 **하나에 한해** `rm -rf -- "$work"`로 정리한다. `set -u` 아래에서 변수가 비었으면 정리를 생략한다. 이전 실행의 흔적 디렉터리는 만지지 않는다.
2. `AUDIT_DIR`(기본 `data/interim/audit`)의 `chain-*.jsonl`만 임시 디렉터리로 복사한다(원본은 읽기만).
3. 고정점 파일(`AUDIT_ANCHORS_PENDING_FILE` 기본값과 `docs/results/audit-anchors.jsonl`)에서 원본 `dir`를 가진 줄 중 `seq`가 가장 큰 줄(가장 최근 줄)을 고른다. 그런 줄이 없으면 메시지와 함께 exit 2.
4. 복사본의 마지막 체인 파일에서 `seq`가 **고른 고정점 줄의 `seq`보다 작은** 레코드만 남긴다(`src/govops/audit/smoke.py`의 `scene5_tail_cut`이 `seq < anchor.seq`로 자르는 것과 같은 방식). 체인 안의 `chain.anchor` 레코드를 기준으로 삼지 않는다.
5. 고른 고정점 줄의 `dir`를 복사본의 저장소 상대 경로로 바꿔 임시 고정점 파일에 쓴다.
6. `uv run python -m govops.audit.verify --dir <복사본> --anchors <임시 고정점>`을 실행하고 출력에 `code=tail_truncated`가 없으면 exit 1.

### 3. `Makefile`

`demo-scene5` 타깃(`.PHONY`, 주석에 "사용자 실행, 복사본에서만 꼬리를 자른다")과 `demo-faults-on`·`demo-faults-off` 타깃(compose `exec -T mock-slack python -m govops.mocks.admin faults set --file /dev/stdin`에 `scripts/demo/mock_faults.json`을 표준 입력으로 / `faults clear`)을 더한다. 기존 타깃·`verify` 레시피는 바꾸지 않는다.

### 4. `tests/test_demo_scripts.py`

합성 체인(임시 키·임시 `AuditLog`, 중간에 `AuditLog.anchor`로 고정점을 남기고 그 뒤에 이벤트를 더 쓴다)과 임시 저장소 루트로 스크립트를 실제 실행해(서브프로세스 timeout 명시) **자르기 전 복사본 검증은 PASS, 자른 뒤에는 `tail_truncated`**를 둘 다 단언하고(`smoke.py`의 `after`·`cut`과 같은 구조), 고정점 줄이 없으면 exit 2임을 확인한다. **원본 체인·고정점 파일의 바이트·`mtime_ns`가 불변**임도 확인한다. `mock_faults.json`의 JSON 구조(엔드포인트·nth·action 타입)를 단언한다.

**사용자 실행(이 step에서 실행하지 않는다)**: `make demo-scene5`, `make demo-faults-on`, `make demo-faults-off`.

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
   - 동결 경로(그래프·프롬프트·도구·Guardrail·평가 러너·골든·감사 쓰기·mock·시드)를 바꾸지 않았는가?
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

- 스크립트가 원본 체인·고정점·대기 파일을 수정하거나 지우지 마라. 이유: 증거 파괴 금지, 읽기 전용 원칙.
- `trap`에서 `mktemp -d`가 돌려준 변수 경로 하나(`rm -rf -- "$work"`)를 지우는 것 외의 삭제를 쓰지 마라. 글롭·다른 경로·변수가 비었을 때의 삭제는 금지다(`set -u`, 빈 값이면 정리 생략). 이유: 사용자 환경 오삭제 방지.
- `Makefile`의 `verify` 레시피와 기존 타깃을 바꾸지 마라. 이유: `tests/test_makefile.py`가 verify 레시피 명령 정확 일치를 단언한다(FP-071).
- 테스트에서 `docker compose` 명령이나 `make demo-faults-on`을 실행하지 마라. 이유: 실제 서비스 접속은 사용자 실행 항목이다(AGENTS.md CRITICAL).
- 스크립트 출력에 체인 레코드 내용·해시 전체를 인쇄하지 마라. 이유: 값 비노출 원칙.

## 소비자 점검(시뮬레이션 통과)

스텁 타깃(`demo-scene5`)과 `scripts/demo/` 파일을 추가한 전체 pytest에서 `tests/test_makefile.py`(verify 레시피 명령 정확 일치)와 `scripts/test_*`는 통과했다. 그래서 `tests/test_makefile.py`는 수정 허용 목록에 넣지 않았다(영향 없음).
