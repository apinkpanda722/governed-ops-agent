# Step 9: smoke-audit

## 읽어야 할 파일

- `/AGENTS.md`(명령어 표의 `smoke-*` 항목 형식), `/docs/ADR.md`(ADR-012), `/docs/PRD.md`(M7 데모 장면 5, 성공 기준의 변조 테스트 5종), `/docs/THREAT_MODEL.md`(TH8·V10)
- 이전 step 산출물: `/src/govops/audit/log.py`·`/src/govops/audit/crypto.py`·`/src/govops/audit/verify.py`·`/src/govops/audit/anchor.py`(`AuditLog`, `load_signing_key`, `verify_chain`, `read_anchor_lines`, 대기 파일)
- 기존 smoke 모듈의 출력 형식(`PASS`/`FAIL`/`INFO` 줄, 종료 코드): `/src/govops/gateway/smoke_guardrail.py`, `/src/govops/tools/smoke.py`와 그 테스트(`/tests/test_smoke_gateway.py`·`/tests/test_smoke_tools.py`). `/Makefile`·`/tests/test_makefile.py`
- `/.claude/skills/failure-patterns/SKILL.md`(FP-021·040·050·051) — 읽기만 한다

## 작업

**수정할 파일**(이 목록이 전부다): `src/govops/audit/smoke.py`(신규), `Makefile`, `tests/test_audit_smoke.py`(신규), `tests/test_makefile.py`(새 타깃 단언 추가에 한함, 기존 단언 약화 금지). 계층: `audit/`는 `agent`·`tools`·`broker`·`api`를 import할 수 없다. TDD.

열람 금지 경로: `data/interim/`
수정 허용 경로: `src/govops/audit/smoke.py`, `Makefile`, `tests/test_audit_smoke.py`, `tests/test_makefile.py`

열람 금지의 범위: 이 금지는 **Codex 세션이 `data/interim/`의 파일을 직접 열람·출력·인용하는 것**에 대한 것이다. 이 step이 작성하는 코드나 테스트가 실행 시 `data/interim/` 아래 경로를 프로그램으로 읽거나 쓰는 것(기본 경로 문자열, 기본 `AUDIT_DIR`, 대기 파일 경로 포함)은 금지 대상이 아니다. 단 테스트는 `data/interim/`의 실제 파일 **내용**을 읽지 않고(저장소 보호 스냅샷 픽스처는 파일 이름·크기·`mtime_ns`만 읽는다), 쓰기는 임시 디렉터리에서만 한다.

**배경**: `smoke-audit`은 사용자가 실제 키로 실행하는 점검이다. 네트워크·API 키·LiteLLM이 필요 없다. 실제 키의 권한·짝 일치를 확인하고, 임시 체인에서 변조 5종과 꼬리 잘라내기 장면(M7 장면 5)을 재현하며, 실제 감사 디렉터리가 있으면 읽기 전용으로 검증한다. 실제 감사 디렉터리·실제 대기 파일·`docs/results/`에는 아무것도 쓰지 않는다(임시 디렉터리만 쓴다).

### 1. `python -m govops.audit.smoke`

출력은 한 줄씩 `PASS <이름>`, `FAIL <이름> <고정 코드>`, `INFO <이름> <값>`이고 마지막에 `RESULT PASS|FAIL`. FAIL이 하나라도 있으면 종료 코드 1. 키 내용·경로·레코드 값은 출력하지 않는다.

1. `key`: `load_signing_key()`(환경 변수 경로, 기본 경로)를 호출한다. 성공하면 `PASS key`와 `INFO kid=<12 hex>`·`INFO key_mode=0600`·`INFO key_pair=match`. `AuditKeyError`는 `FAIL key <코드>`를 내고 **이후 단계는 실행하지 않고** 바로 `RESULT FAIL`.
2. `chain_basic`: 임시 디렉터리(저장소 밖, `tempfile`)에 실제 키로 `AuditLog`를 만들고 합성 이벤트(`service.start`·`model.call`·`side_effect.draft` 등 형식에 맞는 합성 값)를 쓰고 `verify_chain`이 통과하면 `PASS chain_basic`(레코드 수·`kid`를 `INFO`로).
3. `tamper_<종류>` 5종: 임시 체인의 복사본마다 `modify`·`delete_middle`·`reorder`·`tail_truncate`·`other_key`를 적용하고 **기대한 문제 코드가 정확히 나올 때만** `PASS tamper_<종류>`(수정=`hash_mismatch`, 중간 삭제=`seq_gap`, 순서 변경=`seq_order` 또는 `prev_hash_mismatch`, 꼬리 잘라내기=`tail_truncated`, 다른 키=`kid_unknown`). 다른 코드이거나 통과하면 `FAIL tamper_<종류> not_detected`.
4. `scene5_tail_cut`(M7 장면 5): 임시 체인에 이벤트 몇 건 → 고정점(`AuditLog.anchor`, 임시 저장소 루트와 임시 대기 파일 사용) → 이벤트 몇 건 더 → 고정점 레코드 앞에서 잘라내기 → `tail_truncated` 탐지를 확인하고 `PASS scene5_tail_cut`. 고정점 줄이 대기 파일에서 읽혀 검증기가 `anchors_checked == 1`로 센 것도 확인한다. 고정점 레코드 뒤만 자른 체인은 통과함을 `INFO known_limit=tail_after_last_anchor_not_detected`로 출력한다(숨기지 않는다).
5. `real_chain`: `AUDIT_DIR`(기본 `data/interim/audit`)에 `chain-*.jsonl`이 있으면 대기 파일·`docs/results/audit-anchors.jsonl`의 고정점과 함께 `verify_chain`을 **읽기 전용**으로 실행해 통과면 `PASS real_chain`(`INFO records=<n> anchors=<k>`), 문제가 있으면 `FAIL real_chain <첫 문제 코드>`. 체인 파일이 없으면 `INFO real_chain=none (first write creates genesis)`이고 FAIL이 아니다.
6. `INFO legacy_unverified files=<f> lines=<l> (체인 없음, 미검증)`: 실제 디렉터리의 legacy 파일을 센다(내용은 읽지 않고 줄 수만 센다).

### 2. Makefile과 테스트

- `Makefile`: `.PHONY: smoke-audit`, 주석("키·네트워크 불필요, 임시 체인에서 변조 5종·꼬리 잘라내기 재현, 실제 키 권한·짝·체인 확인, 사용자 실행"), 타깃 `smoke-audit: uv run python -m govops.audit.smoke`. `.env`를 읽지 않는다.
- `tests/test_audit_smoke.py`(conftest 임시 키·임시 `AUDIT_DIR`): 정상 환경에서 모든 단계 `PASS`·종료 0, `RESULT PASS`. 키 파일 없음·권한 `0644`·공개키 불일치에서 `FAIL key <코드>`·`RESULT FAIL`·종료 1이고 이후 단계 줄이 없다. 변조 단계가 **독립적으로** 실패할 수 있음: 검증기의 한 종류 탐지를 가짜로 끄면(검증기 함수를 대체해 해당 문제 코드를 숨김) 해당 `tamper_*`만 `FAIL`이고 나머지는 `PASS`(FP-021). 실제 체인이 변조된 환경(`AUDIT_DIR`의 체인 파일 한 줄 수정)에서 `FAIL real_chain`·종료 1, 체인이 없으면 `INFO`·종료 0. 출력에 키 PEM·64 hex 해시·128 hex 서명이 없다(스캔). 스모크가 실제 `AUDIT_DIR`의 바이트를 바꾸지 않는다(실행 전후 파일 목록·크기·해시 비교).
- `tests/test_makefile.py`: `smoke-audit` 타깃이 있고 `govops.audit.smoke`를 포함하며 `.env`를 읽지 않는다.

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


- 스모크가 실제 감사 디렉터리·실제 대기 파일·`docs/results/`에 쓰게 하지 마라. 이유: 사용자 체인에 합성 레코드가 섞이거나 원래 폴더의 git 상태가 변한다. 변조·고정점 장면은 임시 디렉터리에서만 만든다.
- 변조 탐지 합격 조건을 "통과하지 않았다"로 두지 마라. 이유: 어떤 오류든 우연히 실패로 보여 가짜 PASS가 된다. 종류마다 기대한 문제 코드가 정확히 나올 때만 PASS이다(FP-021).
- 고정점 뒤만 자른 체인이 탐지되지 않는 한계를 출력에서 빼지 마라. 이유: 5종 탐지가 전부 통과해도 이 한계는 남는다. 한계를 숨기면 보고 수치가 과장된다.
- 키 내용·경로·해시 원문·서명을 출력하지 마라. 이유: 비밀 값 규칙이다. 코드·kid·건수만 낸다.
- `make smoke-audit`·`make audit-verify`·`make audit-anchor`를 실행하지 마라. 이유: 실제 키와 실제 감사 디렉터리를 쓰는 사용자 실행 명령이다. 이 step의 테스트는 임시 디렉터리와 임시 키로만 돈다(FP-050의 "실제 환경 실측은 사용자 몫").
