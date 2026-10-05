# Step 5: effects-docs

## 읽어야 할 파일

- `/AGENTS.md`, `/docs/PRD.md`, `/docs/ADR.md`, `/docs/ARCHITECTURE.md`, `/docs/THREAT_MODEL.md`, `/docs/EVAL.md`, `/tests/test_docs_consistency.py`
- 이전 step 산출물: `/src/govops/mocks/faults.py`, `/src/govops/mocks/admin.py`, `/src/govops/tools/markers.py`, `/src/govops/tools/resilience.py`, `/src/govops/tools/outbox.py`, `/src/govops/tools/effects.py`, `/src/govops/audit/log.py`, `/src/govops/eval/duplicates.py`, `/tests/test_effects_matrix.py`

## 작업

이 phase에서 **확정된 결정을 문서에 반영**한다. 코드는 수정하지 않는다(문서 일관성 테스트가 필요로 하는 경우만 예외). 문서에는 **결정 내용만** 쓴다. 누가 정했는지, 이전에 어땠는지(변경 경위)는 쓰지 않는다. ADR의 "변경 기록" 줄에는 날짜와 확정된 내용만 적는다.

### 1. ADR-018 추가 (`docs/ADR.md` 끝, 기존 ADR 서식을 따른다. 상태 "유효", 날짜 2026-10-05)

제목: "M3 결정 — 장애 처리 값, outbox·표식, 감사 최소 구현, 승인자 정책, 발송 범위 검사". 결정 항목:

1. **값 표**: Soft 3초·Hard 10초는 도구 호출(읽기·쓰기)에만 적용한다. 읽기 재시도 최대 3회 시도(base 0.5초 ×2, 상한 4초, full jitter), `Retry-After` 준수(30초 상한), 서킷은 대상 서비스별(`jira`·`slack`·`a_mcp`)이며 연속 5회 실패 시 열림·30초 뒤 half-open, 표식 2단계 조회 대기 2.0초, 부작용 발송 시도 최대 3회. **모델 호출 타임아웃은 별도 값이며 게이트웨이 phase에서 정한다**(값 표 밖).
2. **표식·멱등 키**: 메타데이터 `govops_idem`·`govops_task`·`govops_approval`. 멱등 키 = sha256(정규화 JSON{task, 도구, 인자}). 조회는 채널 이력·티켓 코멘트·원 티켓 children.
3. **outbox**: Postgres `govops` DB 테이블(키 PRIMARY KEY, `INSERT … ON CONFLICT DO NOTHING`으로 선점), 행에는 해시·mock ID·상태·`audit_gap`만 둔다. 상태 = 기록됨·시도됨·불확실·성공·실패.
4. **재시도 분류**: 연결 수립 실패·429만 표식 조회 없이 재시도(발송 시도 3회 안에서), 그 밖의 불확실 실패(타임아웃·5xx·전송 오류)는 표식 조회 2단계 뒤 두 번 모두 없을 때만 재발송, 4xx는 확정 실패, 조회 불가는 재발송하지 않고 불확실로 종료. 마지막 시도가 불확실 실패로 끝났으면 표식이 보이지 않아도 불확실로 종료하고 `FAILED`는 보내지 않았음이 확실한 경우(4xx, 서킷 열림으로 호출하지 않음, 연결 실패·429만 반복, 첫 발송 전 감사 실패)에만 쓴다. POST마다 직전에 실행 시도 이벤트(발송 번호 포함)를 fail-closed로 기록한다. 재시작 정리는 표식 조회만 하고 재발송하지 않는다.
5. **장애 주입 인터페이스**: mock 관리 CLI `faults set/clear/stats`, `state --bodies`. 규칙은 엔드포인트별 요청 순번이고 종류는 지연·5xx(처리 전/후)·429·처리 후 응답 유실·조회 반영 지연이다. F10은 항상 F5(처리 후 응답 유실)와 결합해 건다(조회 반영 지연만으로는 표식 조회가 일어나지 않는다). F4(연결 수립 실패)는 서버가 아니라 클라이언트 쪽 전송 계층에서 주입한다.
6. **감사 최소 구현**: `audit/`가 비체인 JSONL(`data/interim/audit/`, 0600)을 쓴다. 이벤트 이름은 ADR-012 기록 대상(초안·승인 요청·승인·수정·거부·만료·실행 시도·결과·표식 조회)이다. 실행 전 의도 기록 실패 시 부작용을 실행하지 않고, 결과 기록 실패는 재시도 후 `audit_gap`으로 표시한다. 해시체인·서명은 M4가 같은 인터페이스에 추가한다.
7. **승인자 정책(M3에서 구현)**: 전사 공지(`announce`) 발송은 요청자 팀의 4급 이상이면서 **요청자 본인이 아닌** 사용자만 승인할 수 있다. 요청자가 4급 이상이어도 본인 승인은 403이다. 그 밖의 채널·티켓 작업은 요청자 본인만 승인하며, 다른 사용자의 task는 404다. 순수 함수와 테스트는 M3, 승인함·화면은 M5다.
8. **V15 발송 범위 검사(다음 phase에서 구현, 결정만 기록)**: 보낼 곳 독자 ⊆ 근거 열람 범위. 근거 = 본문 또는 발췌가 모델 입력에 들어간 문서·티켓(`search_docs` 발췌, `get_document`, 티켓 조회 본문)이고 ID만 돌려준 `lookup_id` 결과는 제외한다. 열람 범위는 문서·티켓 ID와 접근 필드(`access_level`·`access_teams`·`access_min_grade`), 사용자 팀·직급만 담은 `data/seed/` 인덱스에서 읽고 인덱스에 없는 근거는 차단한다. 실행 중 검사는 문서 단위이고 평가 정답 선정(`eval.audience`)의 값 기준 판정보다 엄격하다. 잘못 막힌 건은 TCR 실패로 세고 dev 결과 문서에 건수를 보고한다. 에이전트는 `data/eval/`을 읽지 않는다.

**트레이드오프**와 **이유**도 기존 ADR 서식대로 적는다: 비체인 감사는 M4 전까지 변조를 탐지하지 못한다. 클라이언트 쪽 F4 주입은 서버 쪽 연결 거부와 다르다. 대기보다 긴 조회 반영 지연은 중복을 막지 못한다(한계 시연).

### 2. 승인자 정책 M3 이동 (결정 내용만 고친다)

- `docs/PRD.md` M3 행에 "승인자 정책(전사 공지: 요청자 팀 4급 이상, 요청자 본인 제외)"을 넣고, M5 행에서 "전사 공지 승인자 정책"을 뺀다. M3 측정에 "중복 건수·누락 건수·최종 상태"를 넣는다.
- `docs/EVAL.md` "안정성 (M3)"의 승인자 줄: "웹 화면은 M5다. 전사 공지 승인자 정책은 M3에서 검증한다"로 바꾼다.
- `docs/ADR.md` ADR-011(M5 구현 목록)·ADR-013(M5 목록)의 문장에서 승인자 정책을 M3로 옮긴다.
- `docs/ARCHITECTURE.md` "에이전트 상태 전이" 첫 단락의 M3/M5 목록을 같은 방식으로 고친다.
- `docs/THREAT_MODEL.md`에 M5 구현이라고 적힌 승인자 정책 문구가 있으면 같은 방식으로 고친다.
- 저장소 전체에서 `rg "승인자 정책"`으로 남은 M5 표기를 찾아 모두 맞춘다.

### 3. 이 phase가 푼 "미정" 문구 갱신

- `docs/ARCHITECTURE.md`: 2단계 조회 대기 시간, 표식 값 형식, outbox 저장소, 읽기 재시도 값, 장애 주입 CLI 관련 "미정(M3에서 결정)"을 ADR-018 참조로 바꾼다. 디렉토리 구조에 새 모듈(`mocks/faults.py`, `tools/markers.py`·`resilience.py`·`outbox.py`·`effects.py`, `audit/log.py`, `eval/duplicates.py`)과 `data/interim/audit/`를 한 줄씩 추가한다.
- `docs/ADR.md` ADR-016: 장애 주입 명령 "미정"을 ADR-018 참조로 바꾼다. ADR-006의 "2단계 조회의 대기 시간은 미정(M3에서 결정)" 문구도 같은 방식으로 바꾼다.
- `docs/THREAT_MODEL.md` 5절 끝 문장: 값과 대기 시간은 ADR-018이라고 쓴다. **모델 장애 주입 방법**과 **장애 주입 시나리오별 표본 수**("미정(M3에서 결정)")는 다음 phase에서 정하므로 **그대로 둔다**.
- `docs/THREAT_MODEL.md`의 F10 행 "주입 방법"을 "F5(처리 후 응답 유실)와 결합한 조회 반영 지연"으로 고치고, V8·V9 검증 설명에 `tests/test_effects_matrix.py`를 이름으로 연결한다.
- `.env.example`: `AUDIT_DIR` 이름과 한 줄 설명이 이미 있으면 그대로 둔다(없으면 step 3 지침대로 추가).

### 검사

`tests/test_docs_consistency.py`가 통과해야 한다. 이 테스트가 막으면 **테스트를 약화하지 말고** 문서를 맞춘다(테스트가 새 문서 구조 때문에 갱신이 필요하면 그 이유를 최종 응답 summary에 적는다).

## Acceptance Criteria

```bash
make verify   # ruff check + ruff format --check + pytest + web 검사
rg -n "승인자 정책" docs AGENTS.md | head -40   # M5 구현으로 남은 표기가 없는지 눈으로 확인
```

## 검증 절차

1. 위 AC 커맨드를 실행한다.
2. 아키텍처 체크리스트를 확인한다:
   - 문서 사이(PRD·ADR·ARCHITECTURE·THREAT_MODEL·EVAL·AGENTS.md)에 모순이 없는가?
   - 결정 주체·변경 경위 표현("결정했다", "이전에는", "변경") 없이 결정 내용만 적었는가?
   - AGENTS.md CRITICAL 규칙을 위반하지 않았는가? (AGENTS.md는 수정하지 않는다. 모순이 발견되면 summary에 적는다.)
3. 결과를 최종 응답(JSON)으로 보고한다. `phases/` 아래 파일은 수정하지 않고 git commit도 하지 않는다:
   - 성공 → `"status": "completed"`, `"summary": "산출물 한 줄 요약"`
   - 해결할 수 없는 실패 → `"status": "error"`, `"reason": "구체적 에러 내용"`
   - 사용자 개입 필요 → `"status": "blocked"`, `"reason": "구체적 사유"` 후 즉시 중단

## 금지사항

- 코드·테스트를 수정하지 마라(문서 일관성 테스트 갱신 필요 시 예외, 이유를 summary에 적는다). 이유: 이 step은 문서 반영만 다룬다.
- 결정 주체나 변경 경위를 문서에 쓰지 마라. 이유: 문서에는 결정 내용만 쓴다.
- 장애 주입 표본 수와 모델 장애 주입 방법의 "미정"을 채우지 마라. 이유: 다음 phase에서 사전 등록 문서와 함께 정한다.
- `docs/results/hypotheses.md`를 수정하지 마라. 이유: 사전 등록 파일이며 바꾸면 새 등록으로 취급된다.
- `AGENTS.md`, `scripts/`, `phases/`, `web/`, `data/`를 수정하지 마라.
- `.env`와 A의 `data/interim/mcp/tokens.json`을 열거나 출력하지 마라.
- 기존 테스트를 깨뜨리지 마라. 문서 일관성 테스트를 약화하지 마라.
