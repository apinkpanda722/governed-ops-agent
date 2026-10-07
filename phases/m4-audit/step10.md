# Step 10: docs

## 읽어야 할 파일

- `/AGENTS.md`(기술 스택, 아키텍처 규칙의 감사 로그, 명령어 표), `/docs/ADR.md`(ADR-012, ADR-018 6항), `/docs/ARCHITECTURE.md`(감사 로그 절), `/docs/THREAT_MODEL.md`(TH8, V10), `/docs/PRD.md`(감사 로그 항목, 성공 기준, M7 장면 5), `/docs/EVAL.md`
- 이 phase 산출물(현재 사실의 출처): `/src/govops/audit/crypto.py`·`log.py`·`verify.py`·`anchor.py`·`smoke.py`, `/src/govops/api/wiring.py`(`service.start`), `/src/govops/agent/graph.py`(`model.call`), `/src/govops/api/auth.py`(`auth.failed`), `/src/govops/evalrun/anchors.py`, `/Makefile`(`audit-verify`·`audit-anchor`·`smoke-audit`), `/.env.example`
- `/tests/test_docs_consistency.py`(문서 일관성 테스트: 이 파일이 AGENTS.md의 `CRITICAL:` 개수와 `## 명령어` 표 형식을 단언한다)
- `/.claude/skills/failure-patterns/SKILL.md`(FP-060·061) — 읽기만 한다

## 작업

**수정할 파일**(이 목록이 전부다): `docs/ADR.md`, `docs/ARCHITECTURE.md`, `docs/THREAT_MODEL.md`, `docs/PRD.md`, `docs/EVAL.md`, `AGENTS.md`, `tests/test_docs_consistency.py`(새 테스트 함수 추가에 한함, 기존 단언 약화 금지). 코드는 고치지 않는다.

열람 금지 경로: `data/interim/`
수정 허용 경로: `docs/ADR.md`, `docs/ARCHITECTURE.md`, `docs/THREAT_MODEL.md`, `docs/PRD.md`, `docs/EVAL.md`, `AGENTS.md`, `tests/test_docs_consistency.py`

**문서 원칙**: 결정 내용과 현재 사실만 쓴다(결정 주체·경위·"이번에 바꿨다" 같은 서술 없음). 결과 수치(첫 고정점 값·kid·봉인된 legacy 수)는 쓰지 않는다. 결과 문서는 이 phase 뒤에 별도로 쓴다. `docs/results/`는 수정하지 않는다(읽기 전용).

### 1. `docs/ADR.md`

- ADR-012 **결정** 문단을 현재 구현에 맞게 고친다(변경 기록이 아니라 본문): 체인 파일은 `chain-YYYY-MM-DD.jsonl`(UTC 날짜, 날짜가 바뀌어도 `prev_hash` 연속), 기존 `YYYY-MM-DD.jsonl`은 legacy로 두고 첫 쓰기의 `chain.genesis`가 legacy 파일 집합(파일 수·줄 수·마지막 seq·`legacy_sha256`)을 봉인하며 legacy는 "체인 없음(미검증)"으로 따로 보고하고 PASS로 세지 않는다. 기록 형식에 `kid`(공개키 DER SHA-256 앞 12자, 해시 대상에 포함). 잠금은 디렉터리 fd의 `flock(LOCK_NB)` 재시도와 마감 10초(`audit_lock_timeout`, fail-closed), 개행 없는 꼬리는 `audit_chain_corrupt`로 append 거부. 이벤트: 시스템 이벤트(`service.start`·`chain.genesis`·`chain.anchor`·`auth.failed`: `actor.sub="system"`, `task_id=null`), `model.call`(성공한 호출만, 원문 없음, `cost_usd` 고정 6자리 문자열), `auth.failed`(사유 enum, 분당 60건 상한과 `suppressed`). 키: `AUDIT_KEY_FILE`(기본 `data/interim/audit_ed25519.pem`, 0600), 공개키 `data/seed/audit_public_key.pem`(`AUDIT_PUBLIC_KEY_FILE`), 키가 없거나 권한·짝이 틀리면 서비스·러너 시작 거부(`audit_key_missing`·`audit_key_permissions`·`audit_key_mismatch`). 고정점: `make audit-anchor`와 dev·faults 러너가 체인에 `chain.anchor`를 덧붙이고 `data/interim/audit/anchors-pending.jsonl`(gitignore, `AUDIT_ANCHORS_PENDING_FILE`)에 `{dir, seq, hash, sig, kid, label, ts}` 줄을 쓰며, 결과 문서 PR이 이 줄을 `docs/results/audit-anchors.jsonl`로 옮겨 커밋한다. `make audit-verify`는 체인·서명·seq 빈틈·`legacy_sha256`·두 고정점 파일을 `(dir, seq, hash)`로 중복 제거해 대조한다. 변조 테스트 5종과 한계(고정점 레코드 뒤만 자르면 다음 고정점 전까지 탐지 못 함, 같은 키로 전체 재계산은 고정점으로만 탐지)를 적는다. 기존 "실행마다 마지막 seq·hash를 서명해 `docs/results/` manifest에 커밋" 문구는 위 대기 파일 → 결과 PR 방식으로 바꾼다.
- ADR-012 **변경 기록**에 날짜 `2026-10-08`로 항목을 추가한다(기존 항목은 수정하지 않는다): ① 구현 범위(체인·서명·고정점·`audit-verify`·`audit-anchor`·`smoke-audit`) ② 이월 4건: 실패·차단된 모델 호출의 `model.call` 미기록(차단은 `guardrail.input_blocked`, 실패는 비용 원장·trace가 기록), `auth.failed`에서 호출자 sub 식별 없음(항상 `system`), 쓰기 권한 거부는 전용 이벤트 없이 `side_effect.result`의 `reason`으로 갈음, 웹 기록 화면의 `audit-verify` 결과 표시는 M7 ③ 고정점은 대기 파일 → 결과 PR이 `docs/results/audit-anchors.jsonl`로 옮김.
- ADR-018 6항의 "해시체인·서명은 M4가 같은 인터페이스에 추가한다" 문구를 현재 사실("해시체인·서명은 ADR-012")로 바꾼다. ADR-018의 다른 내용은 바꾸지 않는다.
- **주의**: `tests/test_docs_consistency.py`가 ADR-007·ADR-010의 변경 기록 항목 수를 고정한다. 그 두 절은 건드리지 않는다. ADR-012에는 항목 수 단언이 없다.

### 2. 나머지 문서

- `docs/ARCHITECTURE.md` 감사 로그 절: 레코드 예시에 `kid`·`prev_hash`·`hash`·`sig`를 현재 형식으로 두고 파일 이름·`chain.genesis`·잠금·시스템 이벤트·`model.call`·`auth.failed`·고정점 흐름(대기 파일 → 결과 PR)을 쓴다. 마지막 문장("해시체인·서명과 나머지 사건별 추가 필드는 M4 phase 2에서 구현한다")은 현재 사실로 바꾼다.
- `docs/THREAT_MODEL.md` TH8 행과 V10 행: 완화 열의 "실행마다 고정점 커밋"을 "실행마다 고정점(대기 파일 → 결과 PR 커밋)"으로, 검증 열은 변조 5종·`smoke-audit`로 고치고 알려진 한계 열에 위 두 한계를 적는다. 행의 다른 열 의미는 바꾸지 않는다.
- `docs/PRD.md`: 감사 로그 항목(6번)의 고정점 문구를 같은 방식으로 맞춘다. M7 데모 장면 5·성공 기준 문장은 의미가 같으면 바꾸지 않는다.
- `docs/EVAL.md`: 감사 관련 언급이 있으면 위 방식에 맞추고 없으면 바꾸지 않는다.
- `AGENTS.md` `## 명령어`: 한 줄씩 추가한다. 형식은 기존과 같다: `make audit-verify # …(읽기 전용, 사용자 실행)`, `make audit-anchor # …(LABEL=… 필요, 사용자 실행)`, `make smoke-audit # …(사용자 실행)`. **`CRITICAL:` 줄은 추가·삭제하지 않는다**(`tests/test_docs_consistency.py`가 개수 25를 단언한다). 기술 스택의 감사 로그 줄은 이미 맞으면 바꾸지 않는다.

### 3. 테스트

`tests/test_docs_consistency.py`에 새 함수 `test_m4_audit_document_contract`를 **추가**한다(기존 함수는 건드리지 않는다): ADR-012 절에 `chain-`·`kid`·`audit_lock_timeout`·`audit_chain_corrupt`·`anchors-pending.jsonl`·`docs/results/audit-anchors.jsonl`·`legacy_sha256`·`model.call`·`auth.failed`와 변경 기록의 이월 문구 네 개(`model.call`, `sub`, `side_effect.result`, `웹 기록 화면`)가 있고, AGENTS.md `## 명령어`에 `make audit-verify `·`make audit-anchor `·`make smoke-audit ` 줄이 각각 있고 `사용자 실행`을 포함하며 Makefile에 `\n<이름>:\n`이 있다. ARCHITECTURE.md와 THREAT_MODEL.md의 TH8 행에 `anchors-pending`(또는 대기 파일)이 언급돼 있다. AGENTS.md의 `CRITICAL:` 개수는 기존 단언(25)이 이미 지키므로 새 테스트에서 중복하지 않는다.

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


- ADR-012의 기존 변경 기록 항목을 수정하지 마라. 이유: 변경 기록은 덧붙이기만 한다. 본문은 현재 사실로 고치되 기록은 추가한다.
- `AGENTS.md`에 `CRITICAL:` 줄을 추가·삭제하지 마라. 이유: `tests/test_docs_consistency.py`가 개수 25를 단언한다. 새 규칙이 필요해 보이면 단언을 고치지 말고 `blocked`로 보고한다(FP-060).
- 결과 값(첫 고정점의 `seq`·해시·kid, 봉인된 legacy 파일 수)을 문서에 쓰지 마라. 이유: 결과 문서는 사용자의 `smoke-audit`·`audit-anchor` 실행 뒤에 별도로 쓴다. 이 phase 문서에는 결정과 형식만 둔다.
- 문서에 결정 주체·경위("검토 결과 바꿨다" 등)를 쓰지 마라. 이유: 문서는 결정 내용과 현재 사실만 둔다.
- `docs/results/`의 기존 파일과 `docs/results/audit-anchors.jsonl`을 만들거나 수정하지 마라. 이유: 이 phase는 고정점 파일을 만들지 않는다. 결과 문서 PR이 대기 파일 줄을 옮겨 커밋한다.
