# Step 3: wiring-args-key

## 읽어야 할 파일

먼저 아래 파일들을 읽고 프로젝트의 아키텍처와 설계 의도를 파악하라:

- `/AGENTS.md`(CRITICAL: API 키는 `.env`에만, 필요한 변수 이름은 `.env.example`에, 토큰·키 값을 로그·trace에 남기지 않음)
- `/docs/results/hypotheses-m8-tool-audit.md`(4·5·12절, 읽기만 한다)
- `/src/govops/api/wiring.py`(`build_task_service` 46~123행: `service.start` 블록 67~93행과 `except AuditKeyError` 90~91행, `open_task_service` 126~205행의 `register_secret_values` 135~139행)
- `/src/govops/api/rollout_wiring.py`(`build_task_service`를 두 번 부름, 읽기만 한다)
- `/src/govops/audit/argmac.py`·`/src/govops/audit/toolcall.py`(step 1), `/src/govops/agent/graph.py`의 `build_graph(tool_audit=…)`(step 2)
- `/src/govops/audit/crypto.py`(`load_signing_key` 90~121행, `SigningKey`. 읽기만 한다, 동결)
- `/tests/conftest.py`(`isolated_audit_environment` 137~156행), `/tests/e2e_backend.py`(`REAL_ENV` 57~66행, `temporary_credentials` 202행~, `main` 316행~), `/tests/test_e2e_backend.py`(`REAL_ENV` 20~29행과 이름별 거부 테스트 297행~)
- `/tests/test_service_start_event.py`(`test_start_refused`), `/tests/test_api_wiring.py`, `/tests/test_repo_rules.py`(`.env.example` 형식 규칙), `/.env.example`
- `/.claude/skills/failure-patterns/SKILL.md` — 읽기만 한다

## 작업

**수정할 파일**(이 목록이 전부다): `src/govops/api/wiring.py`(`build_task_service`의 키 검사와 기록기 주입에 한함), `tests/test_api_wiring_args_key.py`(신규), `tests/conftest.py`(`isolated_audit_environment`에 합성 키 환경 변수 한 줄 추가에 한함), `tests/e2e_backend.py`(`REAL_ENV`에 이름 하나와 `temporary_credentials`의 반환에 합성 키 하나 추가에 한함), `tests/test_e2e_backend.py`(`REAL_ENV` 사본에 같은 이름 하나 추가에 한함), `.env.example`(주석과 변수 한 줄씩 추가에 한함). TDD.

열람 금지 경로: `data/interim/`, `data/eval/`, `data/redteam/`, `src/govops/guardrails/rules_v1.json`
수정 허용 경로: `src/govops/api/wiring.py`, `tests/test_api_wiring_args_key.py`, `tests/conftest.py`, `tests/e2e_backend.py`, `tests/test_e2e_backend.py`, `.env.example`
열람 금지의 범위: 이 금지는 **Codex 세션이 해당 경로의 파일을 직접 열람·출력·인용하는 것**에 대한 것이다. 이 step의 코드가 런타임에 그 경로를 다루는 것(예: 기본 감사 디렉터리 `data/interim/audit`, 기본 감사 키 경로 `data/interim/audit_ed25519.pem`, `.env`에서 오는 환경 변수 `AUDIT_ARGS_HMAC_KEY`)은 대상이 아니다. 테스트가 `data/interim/`·`data/eval/`·`data/redteam/`의 실제 파일 **내용**을 읽는 것은 여전히 금지이며 테스트는 임시 디렉터리·`tests/conftest.py`의 임시 감사 키·합성 HMAC 키만 쓴다. 예외로 `data/results/`의 실행 디렉터리는 파일 이름과 JSON 키 이름을 확인하는 데 한해 열람할 수 있고, 응답 본문(`answer`·`effect_bodies`)이나 근거 본문을 출력·인용하지 않는다. 이 제한은 이 step이 새로 작성하는 테스트에 한한 것이며, 기존 `tests/test_m7_freeze.py`가 `scripts/freeze_digest.py`로 해시를 계산하며 동결 경로 파일을 바이트로 읽는 것은 `make verify`의 일부이므로 허용한다. Codex 세션이 그 파일 내용을 출력·인용하는 것은 여전히 금지다.

`src/govops/api/` 아래 파일에는 `redteam` 문자열을 쓰지 않는다(`tests/test_redteam_boundary.py`가 `api/`의 모든 `.py`를 검사한다).

**목적**: 운영 서비스·평가 러너·롤아웃 서비스가 모두 거치는 `build_task_service`에서 인자 HMAC 키를 검사하고 기록기를 그래프에 주입한다. 키가 없으면 시작을 거부한다(사용자 결정 4).

### 1. `build_task_service` (`api/wiring.py`)

- **감사 기록(`service.start`) 전에** 키를 검사한다: `signing = load_signing_key()`의 raw 32바이트(`signing.private.private_bytes(Encoding.Raw, PrivateFormat.Raw, NoEncryption())`)를 `load_args_mac(signing_key_raw=…)`에 넘긴다. `AuditKeyError`는 기존과 같이 `StartupError(error.code)`, `ArgsMacKeyError`는 `StartupError(error.code)`(예외 체인 없이 `from None`). 거부된 시작은 감사 디렉터리에 `chain-*.jsonl`을 만들지 않는다.
- 그래프 조립 때 `tool_audit=ToolCallAuditor(audit, mac)`을 넘긴다. 함수 시그니처는 바꾸지 않는다(`semantic` 등 기존 인자 불변). `open_task_service`와 `open_rollout_service`는 `build_task_service`를 거치므로 따로 바꾸지 않는다.
- 키 변수 이름에 `KEY`가 있어 `open_task_service`·`open_rollout_service`의 `register_secret_values`가 trace·로그에서 값을 가린다. 이를 테스트로 확인한다.

### 2. 테스트 환경

- `tests/conftest.py`의 `isolated_audit_environment`에 `monkeypatch.setenv("AUDIT_ARGS_HMAC_KEY", <세션 합성 키>)` 한 줄(세션 고정 `secrets.token_hex(32)`, 감사 서명 키와 다름). 다른 줄은 바꾸지 않는다.
- `tests/e2e_backend.py`: `REAL_ENV`에 `"AUDIT_ARGS_HMAC_KEY"`를 더해 실제 키가 있으면 e2e 백엔드가 거부하게 하고, `temporary_credentials`가 합성 키(`secrets.token_hex(32)`)를 `"AUDIT_ARGS_HMAC_KEY"`로 돌려준다. `tests/test_e2e_backend.py`의 `REAL_ENV` 사본에 같은 이름을 더한다(두 목록은 같아야 한다).
- `.env.example`: `AUDIT_KEY_FILE` 관련 줄 근처에 `# 읽기 도구 호출 인자 HMAC 전용 키(64자리 hex, 감사 서명 키와 다른 값, 사용자 생성, M8 추가)`와 `AUDIT_ARGS_HMAC_KEY=`(값 비움, `tests/test_repo_rules.py` 형식).

### 3. 테스트 `tests/test_api_wiring_args_key.py`

1. 키 없음·공백·형식 오류·감사 서명 키와 같은 값 → `StartupError`의 `audit_args_hmac_key_missing`·`…_invalid`·`…_reused`, `__context__`가 `None`, 감사 디렉터리에 `chain-*.jsonl` 없음, 오류 문자열에 키 hex 없음.
2. 정상 키로 `build_task_service`(가짜 chat·가짜 도구, `tests/test_service_start_event.py`의 `build`와 같은 방식)를 만들고 읽기 호출 2개를 실행하면 감사 체인에 `tool.call` 2개와 `args_hmac_kid`(키에서 독립 계산한 값)가 있고 `verify_chain`이 통과한다.
3. 평가 러너·롤아웃 경로: `open_rollout_service`를 가짜 A로 조립(`tests/test_api_rollout_wiring.py`의 `fake_a` 방식)하면 v1·v2 그래프 모두 기록기를 가진다(완전성). 키가 없으면 `open_task_service`와 `open_rollout_service`가 같은 코드로 거부된다.
4. `register_secret_values`가 등록한 뒤 trace 마스킹(`govops.obs.mask`)이 키 값을 가린다.

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
   - 이 step이 허용한 동결 키 외의 동결 경로(프롬프트·도구·Guardrail·평가 러너·골든·mock·시드·`costs.py`·`infra/litellm/config.yaml`)를 바꾸지 않았는가?
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

- `.env`를 열거나 출력하거나 고치지 마라. 실제 키를 만들지 마라. 이유: 키 생성은 사용자가 한다(AGENTS CRITICAL, 사용자 결정 2). 테스트는 `secrets.token_hex(32)` 합성 키만 쓴다.
- `src/govops/audit/crypto.py`와 `_KEY_CODES`를 수정하지 마라. 이유: 동결 파일이다. 키 오류는 `ArgsMacKeyError`로 따로 둔다(등록 5절).
- 키가 없을 때 기록기 없이 서비스를 띄우거나 `args_hmac` 없이 기록하지 마라. 이유: 사용자 결정 4(조용히 해시 없이 기록하지 않는다).
- `tests/test_service_start_event.py`·`tests/test_api_wiring.py`·`tests/test_api_wiring_build.py`·`tests/test_api_wiring_semantic.py`·`tests/test_api_rollout_wiring.py`를 수정하지 마라. 수정 없이 통과해야 한다. 이유: 조립 경로의 기존 계약(서비스 1개·`service.start` 개수·semantic 호출자 가드) 불변의 증거다. `tests/conftest.py`의 합성 키가 이 테스트들에 키를 제공한다.
- `tests/e2e_backend.py`와 `tests/test_e2e_backend.py`의 `REAL_ENV`를 서로 다르게 두지 마라. 이유: 테스트가 사본으로 이름별 거부를 검사한다(FP-071: 정확 일치 목록은 같은 한 항목 추가에 한함).
