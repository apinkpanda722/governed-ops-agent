# Step 3: audit-verify

## 읽어야 할 파일

- `/AGENTS.md`(감사 로그 규칙), `/docs/ADR.md`(ADR-012), `/docs/THREAT_MODEL.md`(TH8, V10), `/docs/PRD.md`(성공 기준의 변조 테스트 5종)
- 이전 step 산출물: `/src/govops/audit/crypto.py`(`load_public_keys`, `verify_hash`, `record_hash`, `canonical_json`), `/src/govops/audit/log.py`(step 1·2: 체인 레코드 형식, genesis, `chain.anchor` 이벤트), `/tests/audit_keys.py`, `/tests/conftest.py`, `/tests/test_audit_chain.py`
- `/Makefile`(기존 타깃의 `.PHONY`·주석 스타일), `/tests/test_makefile.py`
- `/.claude/skills/failure-patterns/SKILL.md`(FP-021·040) — 읽기만 한다
- 저장소에 커밋된 기존 감사 파일의 형태(원자료: `git ls-files data/results | rg audit`): `data/results/dev/<run>/attempts/<task>/audit/2026-10-06.jsonl` 형태의 비체인 legacy 파일이 커밋되어 있다. 내용은 열람하지 않고 이름 규칙만 안다.

## 작업

**수정할 파일**(이 목록이 전부다): `src/govops/audit/verify.py`(신규), `Makefile`, `tests/test_audit_verify.py`(신규), `tests/test_makefile.py`(새 타깃 단언 추가에 한함, 기존 단언 약화 금지). 계층: `audit/`는 `agent`·`tools`·`broker`·`api`를 import할 수 없다. TDD.

열람 금지 경로: `data/interim/`
수정 허용 경로: `src/govops/audit/verify.py`, `Makefile`, `tests/test_audit_verify.py`, `tests/test_makefile.py`

열람 금지의 범위: 이 금지는 **Codex 세션이 `data/interim/`의 파일을 직접 열람·출력·인용하는 것**에 대한 것이다. 이 step이 작성하는 코드나 테스트가 실행 시 `data/interim/` 아래 경로를 프로그램으로 읽거나 쓰는 것(기본 경로 문자열, 기본 `AUDIT_DIR`, 대기 파일 경로 포함)은 금지 대상이 아니다. 단 테스트는 `data/interim/`의 실제 파일 **내용**을 읽지 않고(저장소 보호 스냅샷 픽스처는 파일 이름·크기·`mtime_ns`만 읽는다), 쓰기는 임시 디렉터리에서만 한다.

**배경**: 검증기는 체인 파일을 읽기만 한다(어떤 파일도 쓰거나 고치지 않는다). 개인키는 필요 없고 공개키만 쓴다. 고정점(anchor) 줄은 다음 step이 만들지만 이 step의 검증기가 먼저 읽고 대조한다. 테스트는 고정점 줄을 `chain.anchor` 레코드에서 직접 만들어 쓴다(줄 형식: `{"dir", "seq", "hash", "sig", "kid", "label", "ts"}`, `dir`은 저장소 루트 기준 상대 경로, 나머지는 해당 `chain.anchor` 레코드의 값).

### 1. `verify.py` API

- `@dataclass(frozen=True) class Problem: code: str; seq: int | None = None`(값·내용을 담지 않는다).
- `@dataclass(frozen=True) class ChainReport: directory: Path; records: int; kid: str | None; problems: tuple[Problem, ...]; anchors_checked: int; legacy_files: int; legacy_lines: int` 와 속성 `ok`(문제 없음).
- `verify_chain(directory: Path, *, public_keys: dict | None = None, anchors: Sequence[dict] = (), repo_root: Path | None = None) -> ChainReport`. `public_keys` 기본값은 `load_public_keys()`. `repo_root` 기본값은 `Path(__file__).resolve().parents[3]`. `anchors`는 이미 읽은 고정점 줄 목록이고, 이 체인 디렉터리의 저장소 기준 상대 경로와 같은 `dir`인 줄만 대조한다.
- **저장소 밖 디렉터리**: `directory`가 `repo_root` 아래가 아니면 예외 없이 고정점 대조를 건너뛰고(`anchors_checked=0`, `anchors`가 주어져도 무시) 나머지 검사는 그대로 한다. CLI 출력의 `dir=` 값은 저장소 밖이면 절대 경로 대신 `<outside-repo>`로 쓴다(홈 경로 노출 방지).
- 문제 코드(고정): `unterminated_tail`(파일 끝에 개행 없음), `invalid_json`, `invalid_record`(필드 누락·타입 오류), `hash_mismatch`(레코드 재계산 해시 불일치), `prev_hash_mismatch`, `seq_gap`, `seq_order`(seq가 늘지 않음·중복), `kid_unknown`(공개키 목록에 없는 kid), `sig_invalid`, `genesis_missing`(첫 레코드가 `chain.genesis`가 아니거나 `seq`·`prev_hash` 규칙 위반), `legacy_sha256_mismatch`(genesis가 봉인한 legacy 파일 집합의 현재 해시와 다름. 파일 수·줄 수·마지막 seq 불일치도 같은 코드), `anchor_missing`(고정점의 seq가 체인에 없음), `anchor_hash_mismatch`(그 seq의 해시·서명·kid가 다르거나 이벤트가 `chain.anchor`가 아님), `anchor_sig_invalid`, `tail_truncated`(체인의 마지막 seq가 고정점 seq보다 작음), `anchor_dir_missing`(고정점 줄의 `dir`에 `chain-*.jsonl`이 없음), `no_chain`(디렉터리에 `chain-*.jsonl`이 없음).
- 검사 순서: 파일을 이름순으로 읽는다. 레코드마다 ① JSON·필드 ② `canonical_json`·`record_hash`로 해시 재계산 ③ `prev_hash`가 앞 레코드의 `hash`와 같음(첫 레코드는 `0`×64) ④ `seq`가 앞보다 정확히 1 큼(첫 레코드 제외) ⑤ kid가 공개키 목록에 있고 `verify_hash`가 참. 문제는 **발견한 것을 모두** 모은다(첫 문제에서 멈추지 않는다). 변조는 단일 레코드의 해시 불일치로 끝나지 않고 뒤 레코드의 `prev_hash` 불일치로도 나타나야 한다.
- **고정점 대조**: 각 고정점에 대해 체인에서 같은 `seq`를 찾고(없고 체인의 마지막 seq가 더 작으면 `tail_truncated`, 아니면 `anchor_missing`), 그 레코드의 `hash`·`sig`·`kid`가 고정점 줄과 같고 이벤트가 `chain.anchor`이며 서명이 유효한지 본다. 같은 `(dir, seq, hash)` 줄은 한 번만 센다.
- **legacy 보고**: genesis가 봉인한 legacy가 있으면 현재 `????-??-??.jsonl` 파일 집합(디렉터리 안)의 `legacy_sha256`을 genesis와 같은 방식(`sha256(canonical_json([[파일명, 파일바이트sha256], …]))`, 파일명 오름차순)으로 재계산해 비교한다. **legacy 레코드 자체는 검증하지 않으며** 보고에 "체인 없음(미검증)"으로 파일·줄 수만 따로 싣는다. legacy는 PASS 개수에 들어가지 않는다.
- **고정점의 dir은 모두 검증 대상**: `verify_anchor_dirs(anchors, *, repo_root, verified: Collection[Path]) -> list[Problem]`. 읽은 고정점 줄마다(중복 제거 뒤) `repo_root / dir`에 `chain-*.jsonl`이 하나도 없으면 `anchor_dir_missing`을 돌려준다(그 디렉터리가 `--dir`·`--scan`으로 검증됐는지와 무관하다: 체인 전체 삭제가 0건 검사 통과가 되지 않게 한다). 이미 `verified`에 있는 디렉터리는 다시 검사하지 않는다. CLI는 두 고정점 파일의 줄 전체에 이 검사를 적용하고 문제마다 `FAIL anchor dir=<상대경로> code=anchor_dir_missing`을 출력해 종료 1로 만든다.
- `read_anchor_lines(paths: Sequence[Path]) -> list[dict]`: 파일이 없으면 건너뛰고, 있는데 줄이 JSON이 아니거나 필드·형식(`seq` 정수, `hash` 64 hex, `sig` 128 hex, `kid` 12 hex, `label` 형식)이 틀리면 `ValueError("anchor_file_invalid")`(검증 실패로 취급). `(dir, seq, hash)`로 중복을 제거한다.

### 2. 저장소 스캔과 CLI (`python -m govops.audit.verify`)

- 옵션: `--dir`(기본 `env_or("AUDIT_DIR", "data/interim/audit")`), `--public-key`(기본 `AUDIT_PUBLIC_KEY_FILE`), `--anchors`(여러 번 가능, 기본 두 파일: `env_or("AUDIT_ANCHORS_PENDING_FILE", "data/interim/audit/anchors-pending.jsonl")`와 `docs/results/audit-anchors.jsonl`), `--repo-root`(기본 저장소 루트), `--scan`(여러 번 가능, 저장소 기준 디렉터리: 아래 트리를 훑는다).
- 기본 대상(`--dir`)은 **반드시 체인을 가져야 한다**: `chain-*.jsonl`이 없으면 `FAIL no_chain`(0건 통과를 성공으로 세지 않는다).
- `--scan`: 각 디렉터리 트리에서 `chain-*.jsonl`을 가진 모든 디렉터리를 같은 방식으로 검증한다(그 디렉터리에 대응하는 고정점이 있으면 대조). 그리고 이름이 `????-??-??.jsonl`인 파일만 있고 `chain-*.jsonl`이 없는 `audit` 디렉터리는 legacy(미검증)로 센다. 스캔 대상이 없거나 비어 있어도 실패가 아니다.
- 출력(한 줄씩, 레코드 내용·필드 값은 출력하지 않는다): `PASS chain dir=<상대경로> records=<n> kid=<kid> anchors=<k>`, `FAIL chain dir=<상대경로> code=<코드> seq=<n|->`(문제마다), `INFO legacy_unverified dirs=<d> files=<f> lines=<l> (체인 없음, 미검증)`, 마지막에 `SUMMARY chains_pass=<x> chains_fail=<y> legacy_unverified_files=<f> anchors_checked=<k>`. 종료 코드: FAIL이 하나라도 있으면 1, 아니면 0. 고정점 줄이 0개이면 `INFO anchors=0 (꼬리 잘라내기는 고정점이 있어야 탐지된다)`를 함께 출력한다.
- `Makefile`: `.PHONY: audit-verify`, 주석("읽기 전용·키 불필요, 저장소 `data/results/`의 커밋된 체인과 legacy도 함께 보고, `AUDIT_DIR`은 환경 변수로만 바꾼다"), 타깃 `audit-verify: uv run python -m govops.audit.verify --scan data/results`. 이 타깃은 `.env`를 읽지 않는다(`--env-file` 없음).

### 3. 테스트 (`tests/test_audit_verify.py`)

모든 체인은 실제 `AuditLog`(conftest 임시 키)로 임시 디렉터리에 만든다. 변조는 파일을 직접 고쳐서 만든다. 각 변조는 **서로 다른 문제 코드**로 나타남을 단언한다(FP-021).

1. 정상 체인(시스템·일반 이벤트·UTC 날짜 경계 두 파일)은 `ok`이고 CLI가 `PASS`·종료 0, 변조가 없는 체인에서 `problems == ()`.
2. **수정**: 한 레코드의 필드 값을 바꾸면 `hash_mismatch`(해당 seq)이고 뒤 레코드에 `prev_hash_mismatch`가 있다. 해시까지 다시 계산해 쓰면 `sig_invalid`와 뒤 레코드의 `prev_hash_mismatch`.
3. **중간 삭제**: 레코드 하나를 지우면 `seq_gap`과 `prev_hash_mismatch`.
4. **순서 변경**: 인접한 두 줄을 바꾸면 `seq_order`(또는 `prev_hash_mismatch`와 함께).
5. **꼬리 잘라내기**: 고정점이 있는 체인에서 고정점 레코드와 그 뒤를 잘라내면 `tail_truncated`. 고정점 레코드는 두고 그 뒤만 자르면 **탐지되지 않는다**(PASS, 한계로 테스트에 명시하고 `# 한계: 다음 고정점 전까지 탐지 못 함` 주석). 고정점 줄이 없으면 어떤 꼬리 잘라내기도 탐지되지 않음과 `anchors=0` 안내 출력.
6. **다른 키 서명**: 전체 체인을 다른 키로 해시 재계산·재서명하고 `kid`도 그 키의 것으로 쓰면 `kid_unknown`. `kid`는 원래 것을 두고 다른 키로 서명하면 `sig_invalid`. 같은 키로 전체를 재계산(변조 후 해시·서명 다시 쓰기)하면 체인 자체는 통과하지만 **고정점이 있으면** `anchor_hash_mismatch`로 탐지된다(고정점 없이는 못 막는 한계를 주석으로 명시).
6-1. **체인 전체 삭제**: 고정점이 있는 체인 디렉터리의 `chain-*.jsonl`을 모두 지우면(고정점 줄은 남음) CLI가 `FAIL anchor … anchor_dir_missing`·종료 1이다(`--scan`·`--dir`이 그 디렉터리를 가리키지 않아도 같다). 고정점 줄이 가리키는 디렉터리가 정상 체인이면 이 코드가 나오지 않는다.
7. 개행 없는 꼬리 → `unterminated_tail`(append 거부와 구별되는 검증기 사유). genesis 삭제·첫 레코드 변경 → `genesis_missing`.
8. legacy: genesis가 봉인한 파일 집합이 바뀌면(파일 추가·수정·삭제) `legacy_sha256_mismatch`. 봉인된 legacy가 그대로이면 통과하며 보고의 `legacy_files`·`legacy_lines`가 맞다. legacy는 PASS 개수에 포함되지 않고 `INFO legacy_unverified`로만 나온다.
9. 고정점 줄: 정상 줄 → `anchors_checked`가 1, 같은 줄이 두 파일에 있어도 한 번만 센다, 형식이 틀린 줄·JSON이 아닌 줄 → CLI가 `FAIL`·종료 1(`anchor_file_invalid`), `dir`이 다른 줄은 무시.
10. `--scan`: 임시 저장소 루트에 체인 디렉터리 2개(정상 1·변조 1)와 legacy만 있는 `audit` 디렉터리 1개를 두면 종료 1이고 정상 체인은 `PASS`, 변조 체인은 `FAIL`, legacy는 `INFO`로 따로 나온다. 스캔 대상 디렉터리가 없어도 종료 0(기본 대상 체인이 정상일 때).
11. **두 프로세스가 쓴 체인**(`spawn` 2개 × N=10건, 모든 대기에 `timeout`)을 이 step의 검증기로 검증하면 `ok`이고 `records`가 2N+1이다.
12. 출력·보고에 레코드의 필드 값이나 해시 원문·서명이 나오지 않는다(출력 문자열 스캔: 64 hex·128 hex 패턴 없음. 단 `PASS`/`FAIL` 줄의 `dir`·`kid`(12 hex)는 허용).
13. 저장소 밖 디렉터리(임시 `repo_root`와 다른 위치의 체인): `verify_chain`이 예외 없이 `ok`이고 `anchors_checked == 0`(해당 `dir`을 가진 줄이 있어도 무시), CLI 출력에 절대 경로가 없고 `dir=<outside-repo>`이며 종료 0.
14. `tests/test_makefile.py`: `audit-verify` 타깃이 있고 `.env`를 읽지 않으며 `--scan data/results`를 포함.

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


- 검증 결과가 "문제 0건"이라는 이유만으로 통과로 세지 마라. 이유: 체인이 없는 디렉터리·고정점이 0개인 검증은 아무것도 확인하지 않은 성공이다. 기본 대상은 `no_chain`으로 실패하고 고정점 0개는 안내를 출력한다(FP-040).
- 변조 종류마다 서로 독립인 문제 코드를 단언하라. 이유: 한 가지 신호(예: `hash_mismatch`)가 다른 변조에서도 우연히 만족되면 5종을 구별하지 못한 채 통과한다(FP-021).
- 검증기가 파일을 쓰거나 고치거나 이동하게 하지 마라. 이유: 증거를 훼손하면 안 된다. 읽기만 한다.
- 출력에 레코드 필드 값·서명·해시 원문을 내보내지 마라. 이유: 감사 로그 내용은 해시와 ID뿐이지만 출력 경로는 더 넓다. 코드·건수·kid·상대 경로만 낸다.
- `make audit-verify`·`make audit-anchor`·`make smoke-audit`을 실행하지 마라. 이유: 이 step의 테스트는 임시 디렉터리와 임시 키로만 돌고, 사용자 체인과 키는 사용자가 실행한다(저장소의 `data/interim/audit`을 건드리면 conftest 세션 스냅샷 단언이 실패한다).
