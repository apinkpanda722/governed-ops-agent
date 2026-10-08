# Step 0: audit-crypto

## 읽어야 할 파일

- `/AGENTS.md`(계층 경계 표의 `audit/` 행, 고정 제약), `/docs/ADR.md`(ADR-012), `/docs/ARCHITECTURE.md`(감사 로그 절)
- `/src/govops/audit/log.py`·`/src/govops/audit/__init__.py`(현재 비체인 JSONL 구현), `/src/govops/env.py`(`env_or`), `/tests/conftest.py`(기존 픽스처·세션 팩토리), `/tests/test_env_defaults.py`(변수 기본값 표), `/.env.example`
- `/data/seed/audit_public_key.pem`(커밋된 공개키: 이 파일의 존재만 알면 된다), `/pyproject.toml`(`cryptography>=50.0.2`가 이미 직접 의존성이다)
- `/.claude/skills/failure-patterns/SKILL.md`(FP-030) — 읽기만 한다
- **외부 계약 실측**(로컬 `cryptography` 50.0.2, 일회용 프로브. **성공 여부와 반환 내용을 따로 적는다**):

| 지점 | 성공 여부 | 반환 내용 |
|---|---|---|
| `Ed25519PrivateKey.generate()`·`sign`·`verify` | 동작 | 서명은 64바이트이고 같은 입력이면 결정적이다. 틀린 메시지 검증은 `cryptography.exceptions.InvalidSignature`를 던진다 |
| 공개키 DER(`Encoding.DER`, `PublicFormat.SubjectPublicKeyInfo`) | 동작 | 44바이트. kid = 이 DER의 SHA-256 hex 앞 12자 |
| PEM 왕복(`PrivateFormat.PKCS8`, `NoEncryption`) | 동작 | 개인키 PEM을 `load_pem_private_key`로 읽으면 같은 공개키 DER가 나온다 |
| 키 파일 권한 | 사용자 사전 작업이 0600으로 만든다 | `stat().st_mode & 0o777 == 0o600`이 아니면 거부 대상이다 |

## 작업

**수정할 파일**(이 목록이 전부다): `src/govops/audit/crypto.py`(신규), `tests/audit_keys.py`(신규), `tests/test_audit_crypto.py`(신규), `tests/test_audit_isolation.py`(신규), `tests/conftest.py`, `tests/test_env_defaults.py`(새 변수 행 추가에 한함, 기존 단언 약화 금지), `.env.example`. 계층: `audit/`는 `agent`·`tools`·`broker`·`api`를 import할 수 없다. 서드파티는 `cryptography`만 쓴다. TDD.

열람 금지 경로: `data/interim/`
수정 허용 경로: `src/govops/audit/crypto.py`, `tests/audit_keys.py`, `tests/test_audit_crypto.py`, `tests/test_audit_isolation.py`, `tests/conftest.py`, `tests/test_env_defaults.py`, `.env.example`

열람 금지의 범위: 이 금지는 **Codex 세션이 `data/interim/`의 파일을 직접 열람·출력·인용하는 것**에 대한 것이다. 이 step이 작성하는 코드나 테스트가 실행 시 `data/interim/` 아래 경로를 프로그램으로 읽거나 쓰는 것(기본 경로 문자열, 기본 `AUDIT_DIR`, 대기 파일 경로 포함)은 금지 대상이 아니다. 단 테스트는 `data/interim/`의 실제 파일 **내용**을 읽지 않고(저장소 보호 스냅샷 픽스처는 파일 이름·크기·`mtime_ns`만 읽는다), 쓰기는 임시 디렉터리에서만 한다.

**배경**: 이 phase는 감사 로그를 해시체인 + Ed25519 서명으로 바꾼다(ADR-012). 이 step은 그 암호 부품과 테스트 격리 장치만 만든다. 기록 형식 전체는 다음 step들이 쓴다: `{seq, ts, prev_hash, actor:{sub,act}, event, task_id, …필드, kid, hash, sig}`, `hash` = SHA-256 hex(정규화 JSON(`hash`·`sig` 제외, `kid` 포함)의 UTF-8 바이트), `sig` = Ed25519 서명 hex(서명 대상은 `hash` hex 문자열의 ASCII 바이트).

### 1. `govops/audit/crypto.py` (순수 함수, `govops` 다른 계층 import 금지)

- `class AuditKeyError(Exception)`: 속성 `code`(고정 코드)만 가진다. `str(error)`는 코드 그대로다. 코드 집합: `audit_key_missing`, `audit_key_permissions`, `audit_key_invalid`, `audit_key_mismatch`, `audit_public_key_missing`, `audit_public_key_invalid`. 경로·키 바이트·PEM 내용은 메시지에 넣지 않는다.
- 상수 `DEFAULT_KEY_FILE = "data/interim/audit_ed25519.pem"`, `DEFAULT_PUBLIC_KEY_FILE = "data/seed/audit_public_key.pem"`. 환경 변수 `AUDIT_KEY_FILE`·`AUDIT_PUBLIC_KEY_FILE`은 **`govops.env.env_or(name, default)`로만** 읽는다(미설정·빈 값·공백이면 기본값, FP-030). 상대 경로는 현재 작업 디렉터리 기준이다(다른 감사 설정과 같다).
- `@dataclass(frozen=True) class SigningKey: private; public; kid: str` 와 메서드 `sign(hash_hex: str) -> str`(Ed25519 서명을 128자 hex로).
- `load_signing_key(key_file: Path | None = None, public_key_file: Path | None = None) -> SigningKey`: 순서대로 검사한다. 개인키 파일 없음 → `audit_key_missing`. 권한이 정확히 `0o600`이 아님 → `audit_key_permissions`. PEM이 PKCS8 Ed25519 개인키가 아님 → `audit_key_invalid`. 공개키 파일 없음 → `audit_public_key_missing`, 공개키가 Ed25519가 아님 → `audit_public_key_invalid`. 개인키에서 나온 공개키 DER와 커밋된 공개키 DER가 다르면 → `audit_key_mismatch`.
- `load_public_keys(public_key_file: Path | None = None) -> dict[str, Ed25519PublicKey]`: 검증 전용(개인키 불필요). 키는 kid. 지금은 파일 하나이므로 항목 1개다.
- `kid_of(public) -> str`(DER SHA-256 hex 앞 12자), `verify_hash(public, hash_hex: str, sig_hex: str) -> bool`(서명 불일치·형식 오류는 예외 없이 `False`).
- `canonical_json(value) -> str`: `json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)`. **float 값은 어디에 있든 `TypeError`**(금액은 문자열로만 쓴다). `NaN`·`inf`는 `ValueError`.
- `record_hash(record: dict) -> str`: `hash`·`sig`가 있으면 `ValueError`, 아니면 `canonical_json(record)`의 UTF-8 SHA-256 hex.
- `format_usd(amount: float | Decimal | int) -> str`: 정확히 `^\d+\.\d{6}$` 형식(소수 6자리, `ROUND_HALF_EVEN`). 음수·NaN·inf·bool은 `ValueError`.

### 2. 테스트 격리 (`tests/audit_keys.py`, `tests/conftest.py`)

- `tests/audit_keys.py`: `make_key_pair(directory: Path) -> KeyPair(private_path, public_path, kid)`. `cryptography`로 실제 Ed25519 키 쌍을 만들어 개인키 PEM을 `0600`, 공개키 PEM을 `0644`로 쓴다. 다른 테스트가 import한다(`tests/`가 이미 `sys.path`에 있어 `from audit_keys import make_key_pair`로 쓴다).
- `tests/conftest.py`에 **추가만** 한다(기존 `serve_asgi`·`a_tokens`·`corpus_artifact_factory`는 바꾸지 않는다):
  - 세션 범위 픽스처가 임시 키 쌍을 한 번 만든다.
  - **`autouse` 함수 범위 픽스처**가 `monkeypatch.setenv`로 `AUDIT_DIR`(임시 `…/audit`), `AUDIT_KEY_FILE`, `AUDIT_PUBLIC_KEY_FILE`, `AUDIT_ANCHORS_PENDING_FILE`(임시 `…/anchors-pending.jsonl`)을 모두 임시 경로로 돌린다. 테스트마다 서로 다른 임시 디렉터리를 쓴다. 권한 검사·짝 검사 함수는 **monkeypatch하지 않는다**: 임시 키 쌍이 실제로 `load_signing_key`를 통과해야 한다.
  - **`autouse` 세션 범위 픽스처**가 시작 시 저장소의 `data/interim/audit/`(파일 이름·크기·`mtime_ns` 목록, 디렉터리가 없으면 "없음")와 `docs/results/audit-anchors.jsonl`(없으면 "없음", 있으면 sha256)을 기록하고 세션 종료 시 같은지 단언한다(다르면 어떤 경로가 바뀌었는지 이름만 출력).
- 저장소 루트는 `Path(__file__).resolve().parent.parent`로 구한다.

### 3. 테스트

- `tests/test_audit_crypto.py`: 정상 로드(kid 12자 hex, 서명 → `verify_hash` 참), 틀린 해시·바뀐 서명 → `False`, 파일 없음, 권한 `0644`·`0640`·`0700` 각각 `audit_key_permissions`, PEM이 아닌 내용·RSA 키 → `audit_key_invalid`, 다른 쌍의 공개키 → `audit_key_mismatch`, 공개키 파일 없음. 환경 변수: 테스트 안에서 `AUDIT_KEY_FILE`을 없는 경로로 바꾸면 `audit_key_missing`(픽스처 없이 키 없음 거부), `AUDIT_KEY_FILE=""`이고 `monkeypatch.chdir(임시)`이면 기본 경로로 읽어 `audit_key_missing`. 예외 문자열에 경로·PEM 조각이 없음. `canonical_json`: 키 순서 고정, 공백 없음, 한글 보존(`ensure_ascii=False`), float·`NaN` 거부, 같은 입력 같은 출력. `format_usd`: `0`→`"0.000000"`, `1.23456789`→`"1.234568"`, `Decimal("0.0000005")`→반올림 규칙 고정(`ROUND_HALF_EVEN`), 음수·NaN·inf·bool 거부, 모든 결과가 정규식을 만족(시드 고정 무작위 1,000개).
- `tests/test_audit_isolation.py`: 픽스처가 만든 환경 변수 네 개가 모두 저장소 밖 임시 경로를 가리키고 서로 테스트 간에 다르다. 세션 스냅샷 비교 함수가 파일 추가·크기 변경·삭제를 각각 감지한다(합성 디렉터리로 함수 단위 검증).
- `tests/test_env_defaults.py`: `("audit/crypto.py", "AUDIT_KEY_FILE", "data/interim/audit_ed25519.pem")`, `("audit/crypto.py", "AUDIT_PUBLIC_KEY_FILE", "data/seed/audit_public_key.pem")` 두 행을 표에 추가한다.
- `.env.example`: `AUDIT_KEY_FILE=`, `AUDIT_PUBLIC_KEY_FILE=` 두 줄을 `AUDIT_DIR` 아래에 설명 주석과 함께 추가한다(비워 두면 기본 경로).

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


- 키 파일 내용·PEM·개인키 바이트·키 경로를 예외 메시지·로그·출력·테스트 단언 문자열에 넣지 마라. 이유: 비밀 값 규칙이다. 오류는 고정 코드만 올린다.
- 권한 검사와 공개키 짝 검사를 `monkeypatch`나 우회 플래그로 건너뛰는 코드·픽스처를 만들지 마라. 이유: 임시 키 쌍이 같은 검사를 실제로 통과해야 운영 검사가 테스트된다. 기본 경로를 쓰는 테스트가 저장소에서 임시 키로 체인을 시작하면 이후 실제 키의 기록이 전부 실패한다.
- 환경 변수를 `os.environ.get`·`os.getenv`로 읽지 마라. 이유: 빈 값이 기본값을 덮는다. `env_or`만 쓴다(FP-030).
- 키 생성 CLI·스크립트를 만들지 마라. 이유: 키는 사용자가 `openssl`로 만든다.
- `tests/conftest.py`의 기존 픽스처 동작을 바꾸지 마라. 이유: 모든 테스트가 쓴다. 추가만 한다.
