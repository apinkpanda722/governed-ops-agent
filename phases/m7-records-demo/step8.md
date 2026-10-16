# Step 8: docs-readme-blog

## 읽어야 할 파일

먼저 아래 파일들을 읽고 프로젝트의 아키텍처와 설계 의도를 파악하라:

- `/AGENTS.md`(계층 경계, CRITICAL 규칙)
- `/docs/ARCHITECTURE.md`, `/docs/ADR.md`, `/docs/THREAT_MODEL.md`
- `/docs/M7_DESIGN_DRAFT.md`(이 phase의 승인된 설계), `/docs/results/hypotheses-m7-records-authz.md`(사전 등록, 읽기만 한다)
- `/.claude/skills/failure-patterns/SKILL.md` — 읽기만 한다
- `/README.md`(현재 M0 시점 문구가 남아 있다)
- `/docs/PRD.md`(M7 행), `/docs/EVAL.md`
- `/docs/results/2026-10-07-m3-faults-live.md`, `/docs/results/2026-10-15-m6-redteam-run-live.md`, `/docs/results/2026-10-08-m4-audit-live.md`, `/docs/results/2026-10-13-m6-limits-delete-live.md`(수치 출처, 읽기만 한다)
- `/tests/test_docs_consistency.py`, `/.env.example`

이전 step에서 만들어진 코드를 꼼꼼히 읽고, 설계 의도를 이해한 뒤 작업하라.

## 작업

**수정할 파일**(이 목록이 전부다): `README.md`, `LICENSE`, `.env.example`, `docs/DEMO.md`, `docs/BLOG_DRAFT.md`, `docs/PUBLIC_RELEASE_CHECKLIST.md`, `docs/ARCHITECTURE.md`, `docs/THREAT_MODEL.md`, `docs/ADR.md`, `docs/PRD.md`, `tests/test_docs_consistency.py`. TDD.

열람 금지 경로: `data/interim/`
수정 허용 경로: `README.md`, `LICENSE`, `.env.example`, `docs/DEMO.md`, `docs/BLOG_DRAFT.md`, `docs/PUBLIC_RELEASE_CHECKLIST.md`, `docs/ARCHITECTURE.md`, `docs/THREAT_MODEL.md`, `docs/ADR.md`, `docs/PRD.md`, `tests/test_docs_consistency.py`

열람 금지의 범위: 이 금지는 **Codex 세션이 해당 경로의 파일을 직접 열람·출력·인용하는 것**에 대한 것이다. 이 step의 코드와 테스트가 런타임에 그 경로를 다루는 것은 대상이 아니다. 단 테스트가 `data/interim/`의 실제 파일 **내용**을 읽는 것은 여전히 금지이며 테스트는 임시 디렉터리·합성 데이터·테스트용 키(`tests/conftest.py`의 `isolated_audit_environment`)만 쓴다. 기본 경로 문자열(예: `AUDIT_DIR`의 기본값)이 코드에 있는 것은 대상이 아니다.

**배경**: 문서 정리 step이다. 코드를 바꾸지 않는다. 수치는 결과 문서에서 **복사**하고 다시 계산하지 않는다. 출처 없는 수치를 넣지 않는다.

### 1. 프로필 문서 정정(문서만)

- `docs/ARCHITECTURE.md`의 "`PROFILES = {writes_org}`인 읽기 전용 단일 레지스트리를 사용한다. `GOVOPS_AGENT_PROFILE` 허용값과 기본값은 `writes_org`다." 문장을 사실에 맞게 고친다: `PROFILES`는 `writes_org`(동결)·`writes_team`을 담은 읽기 전용 레지스트리이며 `GOVOPS_AGENT_PROFILE` 기본값은 `writes_team`이다(`api/wiring.py`, ADR-024). 나머지 문장은 유지한다.
- `.env.example`의 프로필 주석(`# 에이전트 프로필: writes_org(기본, …)`)을 `writes_team` 기본·`writes_org` 동결로 고친다. 변수 이름·값은 바꾸지 않는다.
- `tests/test_docs_consistency.py`는 위 문장의 마커 두 개(`PROFILES = {writes_org}`, `기본값은 `writes_org``)를 단언한다. **이 두 마커를 새 문장의 마커(`writes_team`이 기본값임을 담은 구절)로 바꾸는 데 한한다.** 다른 마커·단언은 약화하지 않는다.
- **과거 ADR 본문(ADR-001~027)은 수정하지 않는다.**

### 2. ADR-028 (`docs/ADR.md` 끝에 추가만)

`### ADR-028: M7 결정 — 기록 화면과 감사 검증 노출`을 추가한다. 상태·날짜·결정·이유·트레이드오프·변경 기록 형식은 ADR-025를 따른다. 결정: (1) 기록 화면은 대화 소유자만 읽고 타인·없음·삭제됨·형식 오류·승인자·정리된 대화는 같은 404 `conversation_not_found`다 (2) 전역 검증 요약은 인증된 모든 사용자에게 개수·고정 코드만 보이며 경로·`kid`·해시·서명은 노출하지 않는다 (3) 턴 색인이 정리되면 감사 레코드가 남아도 화면은 404이고 체인은 CLI로 확인한다 (4) 화면의 PASS는 이 호스트 체인과 작업 트리 고정점 기준이며 마지막 고정점 이후 꼬리·같은 키 전체 재계산·B 호스트 탈취는 막지 못한다 (5) 그래프·프롬프트·도구 설명·Guardrail·평가 러너·골든은 바꾸지 않으며 `tests/test_m7_freeze.py`가 digest로 확인한다. `tests/test_docs_consistency.py`가 `### ADR-0NN:` 단위로 문서를 쪼개 단언하므로 ADR-027 뒤에 정확히 붙인다.

### 3. 나머지 문서

- `README.md` 재작성: 첫 단락에 **"mock 시뮬레이션"**을 명시한다. 목차는 `docs/M7_DESIGN_DRAFT.md` 4-1절. "알려진 한계"에 **"평가 정답지(`data/eval`)는 공식 test 동결 뒤 공개한다. 이후 비교에는 새 held-out 세트가 필요하다"**를 쓴다. 수치는 설계 문서 4-2절 표의 출처 파일·행에서 문구 그대로 복사하고 출처를 병기한다. 라이선스 MIT, A 저장소(A도 같은 라이선스로 공개 예정)와의 관계(live에만 A가 필요, `make verify`는 replay)를 적는다.
- `LICENSE`: MIT 라이선스 전문. 저작권자 이름은 `[저작권자]` 자리표시자로 두고 사용자가 채운다.
- `docs/DEMO.md`: 설계 문서 3절을 옮긴다. 명령은 서버 기동부터 순서대로 **한 블록에 명령 하나**로 쓴다. `make obs-up` 뒤에는 LiteLLM `/health/readiness`가 200이 될 때까지 기다리라고 쓴다. A MCP 서버(8765)를 끄지 말라고 쓴다. 장면 3·4 질의 문구는 설계 문서 0-6절의 것을 그대로 쓰고 **인젝션 본문을 인용하지 않는다**. 장면 3은 "수신처가 요청자가 지정한 팀 채널"을 기대 화면으로 하고 리허설에서 실제로 나온 경로를 적을 빈 칸(`리허설 결과: (촬영 전 기록)`)을 둔다. replay 대체 시 화면·캡션에 "replay"를 표시한다고 쓴다.
- `docs/BLOG_DRAFT.md`: 설계 문서 4-4절 개요. 첫 단락에 mock 시뮬레이션 명시. 수치는 4-2절 출처만.
- `docs/PUBLIC_RELEASE_CHECKLIST.md`: 설계 문서 5절 C1~C13을 체크리스트로 옮긴다(검사 명령은 사용자 실행 항목으로 표시, 공개 신원 결정 항목과 A 저장소 쪽 항목 포함).
- `docs/ARCHITECTURE.md`: 감사 로그 절의 "웹 기록 화면의 검증 결과 표시는 M7이다" 문구를 구현된 사실(API 2개·화면·한계)로 갱신하고 UI 계약에 기록 화면 한 단락을 더한다. 디렉터리 구조에 `audit/records.py`를 한 줄 더한다. `docs/THREAT_MODEL.md`: V10 검증 행에 기록 화면 프로브(`hypotheses-m7-records-authz.md`) 한 줄과 잔여 위험 한 줄(화면 PASS의 범위)을 더한다.
- `docs/PRD.md`: M7 행의 상태만 갱신한다(번호·목표 문구 수정 금지). ADR 번호 참조는 바꾸지 않는다. **M7 행은 한 줄을 유지하고, 그 뒤의 빈 줄 → `M6 선행: …` 문장 → 빈 줄 → `make verify` 단락 순서를 바꾸지 않는다**(`tests/test_docs_consistency.py`가 이 순서를 단언한다).
- **문서 일치 테스트 제약**: (a) `docs/ARCHITECTURE.md`의 `## 알려진 한계` 절과 ADR-025 절에는 `N/M` 형태 수치·`%`·`x.y` 소수를 새로 쓰지 않는다(결과 수치 금지 단언). (b) `## 감사 로그` 절에는 `anchors-pending` 또는 `대기 파일`, `conversation.deleted` 문구를 유지한다. (c) `docs/THREAT_MODEL.md`의 `| TH15 /`·`| V16 ` 행은 수정하지 않는다. V10 행·잔여 위험 절에 줄을 더하되 `| TH8 /` 행의 `anchors-pending`/`대기 파일` 문구는 유지한다. (d) ADR-012·ADR-024~027 절 본문은 그대로 둔다(ADR-012 변경 기록에 `웹 기록 화면` 문구가 있어야 한다).

**사용자 실행(이 step에서 실행하지 않는다)**: 없음.

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

- `docs/results/` 아래 파일을 수정하지 마라. 이유: 결과·사전 등록 파일은 읽기 전용이고 동결 digest에 들어 있다(FP-043).
- ADR-001~027 본문을 수정하지 마라. 이유: 결정 기록 보존. ADR-028 추가만 허용한다.
- README·블로그·DEMO에 출처 없는 수치를 쓰지 마라. 이유: 결과 문서 표현과 달라진 수치는 주장이 된다. 4-2절 표의 출처만 쓴다.
- `tests/test_docs_consistency.py`의 단언을 약화하거나 지우지 마라. 이유: 문서 사이 일치 검사다. 프로필 마커 두 개를 새 문장의 마커로 바꾸는 것만 허용한다(FP-071).
- 인젝션 문서 본문·`data/eval`·`data/redteam` 원문을 문서에 인용하지 마라. 이유: 비열람 규칙과 정답지 노출 방지.

## 소비자 점검(시뮬레이션 통과)

문서 변경(ARCHITECTURE 프로필 문장, ADR 추가, `.env.example` 주석, README·LICENSE·DEMO 추가)을 복사본에 반영해 전체 pytest를 돌렸을 때 `tests/test_docs_consistency.py::test_fault_harness_document_contract`만 실패했다(311~312행의 프로필 마커 두 개). 다른 문서 단언은 영향이 없었다. 따라서 이 step의 수정 허용 목록에는 이 파일만 "마커 두 개 갱신에 한함"으로 넣었다.
