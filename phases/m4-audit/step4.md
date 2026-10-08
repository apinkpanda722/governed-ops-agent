# Step 4: audit-anchors

## 읽어야 할 파일

- `/AGENTS.md`(감사 로그 규칙), `/docs/ADR.md`(ADR-012), `/docs/THREAT_MODEL.md`(TH8·V10), `/docs/PRD.md`(M7 데모 장면 5)
- 이전 step 산출물: `/src/govops/audit/log.py`(step 2: 잠금·체인 쓰기), `/src/govops/audit/verify.py`(step 3: `read_anchor_lines`, 고정점 대조, 줄 형식), `/src/govops/audit/crypto.py`, `/tests/test_audit_verify.py`(고정점 줄을 직접 만드는 헬퍼), `/tests/conftest.py`(임시 `AUDIT_ANCHORS_PENDING_FILE`)
- `/Makefile`·`/tests/test_makefile.py`·`/tests/test_env_defaults.py`·`/.env.example`
- `/.claude/skills/failure-patterns/SKILL.md`(FP-030·031·064) — 읽기만 한다

## 작업

**수정할 파일**(이 목록이 전부다): `src/govops/audit/anchor.py`(신규), `src/govops/audit/log.py`, `Makefile`, `.env.example`, `tests/test_audit_anchor.py`(신규), `tests/test_makefile.py`(새 타깃 단언 추가에 한함), `tests/test_env_defaults.py`(새 변수 행 추가에 한함, 기존 단언 약화 금지). 계층: `audit/`는 `agent`·`tools`·`broker`·`api`를 import할 수 없다. TDD.

열람 금지 경로: `data/interim/`
수정 허용 경로: `src/govops/audit/anchor.py`, `src/govops/audit/log.py`, `Makefile`, `.env.example`, `tests/test_audit_anchor.py`, `tests/test_makefile.py`, `tests/test_env_defaults.py`

열람 금지의 범위: 이 금지는 **Codex 세션이 `data/interim/`의 파일을 직접 열람·출력·인용하는 것**에 대한 것이다. 이 step이 작성하는 코드나 테스트가 실행 시 `data/interim/` 아래 경로를 프로그램으로 읽거나 쓰는 것(기본 경로 문자열, 기본 `AUDIT_DIR`, 대기 파일 경로 포함)은 금지 대상이 아니다. 단 테스트는 `data/interim/`의 실제 파일 **내용**을 읽지 않고(저장소 보호 스냅샷 픽스처는 파일 이름·크기·`mtime_ns`만 읽는다), 쓰기는 임시 디렉터리에서만 한다.

**배경**: 고정점은 체인에 덧붙인 `chain.anchor` 레코드의 `(seq, hash, sig, kid)`를 저장소 밖에도 남겨 꼬리 잘라내기·전체 재계산을 탐지하게 하는 장치다. 쓰기 경로는 원래 폴더에서 `docs/results/`를 건드리지 않도록 **대기 파일**에 덧붙인다(원래 폴더의 git 상태가 변하면 러너 청결 검사와 pull이 깨진다). 이 대기 파일의 줄은 결과 문서 PR이 dev worktree에서 `docs/results/audit-anchors.jsonl`로 옮겨 커밋한다. 이 phase는 `docs/results/audit-anchors.jsonl`을 만들지 않는다.

### 1. 대기 파일과 줄 형식 (`anchor.py`)

- 대기 파일 경로: `env_or("AUDIT_ANCHORS_PENDING_FILE", "data/interim/audit/anchors-pending.jsonl")`(상대 경로는 현재 작업 디렉터리 기준). 파일은 `0600`, 부모 디렉터리는 필요하면 `0700`으로 만든다.
- 줄: `canonical_json({"dir", "seq", "hash", "sig", "kid", "label", "ts"}) + "\n"`. `dir`은 체인 디렉터리의 **저장소 루트 기준 상대 경로**(POSIX 구분자), `seq`·`hash`·`sig`·`kid`·`ts`는 방금 쓴 `chain.anchor` 레코드의 값, `label`은 호출 인자.
- 라벨 형식 `^[a-z0-9][a-z0-9-]{0,39}$`. 위반은 `AuditAnchorError(code="audit_anchor_label_invalid")`.
- `class AuditAnchorError(Exception)`: 속성 `code`만(`str`은 코드). 코드: `audit_anchor_label_invalid`, `audit_anchor_dir_outside_repo`, `audit_anchor_write_failed`.

### 2. `AuditLog.anchor`

- `AuditLog.anchor(label: str, *, repo_root: Path | None = None, pending_file: Path | None = None) -> dict`: `repo_root` 기본값은 `Path(__file__).resolve().parents[3]`, `pending_file` 기본값은 위 환경 변수 경로.
- 순서: ① 라벨 검증 ② 정규화한 `self.directory`가 `repo_root` 아래가 아니면 **체인에 아무것도 쓰기 전에** `audit_anchor_dir_outside_repo` ③ `append("chain.anchor", task_id=None, actor_sub="system", label=label)`(체인 쓰기 실패는 `AuditWriteError` 그대로 올림) ④ 방금 쓴 레코드를 체인에서 읽어(또는 쓰기 경로가 돌려준 값으로) 줄을 만들고 대기 파일에 덧붙임(`fsync`) ⑤ 줄 dict를 돌려준다. 대기 파일 쓰기가 실패하면 `audit_anchor_write_failed`(체인에는 `chain.anchor`가 남는다: 다시 실행하면 새 고정점이 생긴다).
- 대기 파일에 쓰는 동안 체인 잠금을 풀어도 된다(대기 파일은 고정점 후보일 뿐이고 `audit-verify`가 체인과 대조한다). 같은 대기 파일에 여러 프로세스가 동시에 덧붙여도 줄이 섞이지 않도록 한 번의 `write`로 쓴다(`O_APPEND`).

### 3. CLI와 Makefile

- `python -m govops.audit.anchor --label <LABEL> [--repo-root <경로>]`: `--repo-root` 기본값은 저장소 루트(`Path(__file__).resolve().parents[3]`)이고 `anchor(label, repo_root=…)`로 넘긴다. `AuditLog()`(환경 변수 경로)로 `anchor(label)`을 호출하고 **대기 파일에 쓴 것과 같은 줄을 stdout에 한 줄로 출력**한다. 오류는 `FAIL <코드>`(stderr)와 종료 코드 1. 키 문제는 `FAIL <AuditKeyError 코드>`. 다른 어떤 출력도 하지 않는다(키 경로·내용 금지).
- `Makefile`: `.PHONY: audit-anchor`, 주석("사용자 실행. 체인에 고정점을 덧붙이고 대기 파일에 같은 줄을 기록한다. 사용: make audit-anchor LABEL=m4-audit-first. 첫 쓰기는 체인 genesis를 만든다"), 타깃: `LABEL`이 비어 있으면 안내를 stderr에 내고 종료 2, 아니면 `uv run python -m govops.audit.anchor --label "$(LABEL)"`. `.env`를 읽지 않는다.
- `.env.example`에 `AUDIT_ANCHORS_PENDING_FILE=`(비우면 기본 경로)를 설명 주석과 함께 추가한다. `tests/test_env_defaults.py`의 표에 `("audit/anchor.py", "AUDIT_ANCHORS_PENDING_FILE", "data/interim/audit/anchors-pending.jsonl")` 행을 추가한다.

### 4. 테스트 (`tests/test_audit_anchor.py`)

- 저장소 루트 역할의 임시 디렉터리를 `repo_root`로 넘기고 체인 디렉터리를 그 아래에 둔다. 대기 파일은 임시 경로다.
- 정상: `anchor("m4-audit-first")`가 `chain.anchor` 레코드를 덧붙이고(첫 호출이면 genesis도 생김), 대기 파일에 정확히 한 줄(0600), 돌려준 dict와 줄이 같고 `dir`이 상대 POSIX 경로이며 `seq`·`hash`·`sig`·`kid`가 체인의 `chain.anchor` 레코드와 같다. 같은 줄을 `verify_chain(..., anchors=read_anchor_lines([대기 파일]))`에 넣으면 `ok`이고 `anchors_checked == 1`.
- **M7 장면 5 재현**: 체인에 이벤트 몇 건 → `anchor("scene-five")` → 이벤트 몇 건 더 → 체인 파일을 고정점 레코드 **앞**에서 잘라낸다(고정점 레코드 포함 이후 삭제) → `verify_chain`이 `tail_truncated`를 보고하고 CLI가 `FAIL`·종료 1. 같은 체인을 고정점 레코드 뒤에서만 잘랐을 때는 탐지되지 않음(한계, 주석 명시).
- 거부: 라벨 형식 위반(대문자·빈 문자열·41자·시작이 `-`) → `audit_anchor_label_invalid`이고 체인과 대기 파일 모두 바뀌지 않음. 체인 디렉터리가 `repo_root` 밖이면 `audit_anchor_dir_outside_repo`이고 **체인 파일이 생기지 않으며** 대기 파일도 바뀌지 않음. 대기 파일 쓰기 실패(부모 경로가 파일인 경우 등) → `audit_anchor_write_failed`이고 체인에는 `chain.anchor`가 남아 있음.
- 두 번 호출하면 줄이 둘이고 `seq`가 다르다. 두 프로세스(`spawn`, 모든 대기에 `timeout`)가 동시에 `anchor`를 호출해도 대기 파일의 모든 줄이 JSON으로 읽히고 체인 검증이 `ok`.
- CLI: 임시 저장소 루트를 만들고 `AUDIT_DIR`을 그 아래로 `monkeypatch`한 뒤(conftest의 기본 `AUDIT_DIR`은 저장소 밖이라 그대로 두면 `audit_anchor_dir_outside_repo`가 된다) `main(["--label", "x", "--repo-root", <임시 루트>])`가 줄을 stdout에 출력하고 대기 파일의 마지막 줄과 같다. `--repo-root`를 생략하고 `AUDIT_DIR`이 저장소 밖이면 `FAIL audit_anchor_dir_outside_repo`·종료 1. 라벨 오류·키 오류(`AUDIT_KEY_FILE`을 없는 경로로)에서 stderr에 코드만 출력하고 종료 코드 1이며 stdout에 아무것도 없다.
- `tests/test_makefile.py`: `audit-anchor` 타깃이 있고 `LABEL` 확인과 `govops.audit.anchor`를 포함하며 `.env`를 읽지 않는다.

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


- 고정점을 `docs/results/` 아래에 직접 쓰지 마라. 이유: 원래 폴더에서 `docs/results/`를 쓰면 러너의 git 청결 검사가 실패하고 pull이 충돌한다. 대기 파일(`data/interim/`, gitignore)에 쓰고 결과 PR이 옮긴다(FP-031·064).
- 체인 디렉터리가 저장소 밖이면 고정점을 만들지 마라. 이유: 상대 경로 `dir`을 정할 수 없고 홈 경로가 커밋에 남을 수 있다. 체인에 쓰기 전에 거부한다.
- `AUDIT_ANCHORS_PENDING_FILE`을 `os.environ.get`·`os.getenv`로 읽지 마라. 이유: 빈 값이 기본값을 덮는다. `env_or`만 쓴다(FP-030).
- 대기 파일 줄에 라벨 외의 자유 문자열·절대 경로·사용자 이름을 넣지 마라. 이유: 이 줄은 커밋되는 증거다. `dir`은 상대 경로, 나머지는 해시·서명·kid·형식이 정해진 라벨·시각뿐이다.
- `make audit-anchor`·`make audit-verify`·`make smoke-audit`을 실행하지 마라. 이유: 사용자 체인에 고정점이 실제로 쓰인다. 이 step의 테스트는 임시 디렉터리와 임시 키로만 돈다.
